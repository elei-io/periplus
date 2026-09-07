"""One bounded physical attempt, followed by fenced frontier outcome acceptance."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
import logging

from periplus.crawl.acquisition.destination import public_destination_url, DestinationRejected, DestinationUnavailable
from periplus.crawl.acquisition.capture import connect_cdp
from periplus.crawl.acquisition.context import AcquisitionContext
from periplus.crawl.acquisition.errors import RetryableAcquisitionFailure, PlaywrightRuntimeLost, CdpUnavailable
from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionResult
from periplus.crawl.acquisition.service import acquire_page
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.runtime.domain_pacing import DomainCapacityUnavailable, domain_permit, try_domain_start
from periplus.crawl.runtime.frontier_queue import CaptureWork
from periplus.crawl.runtime.frontier_evidence import acquisition_context
from periplus.crawl.runtime.frontier_store import FrontierStore, StaleDispatch
from periplus.crawl.runtime.navigation import build_navigation_package, put_navigation_package
from periplus.platform.messaging.leases import operation_leases, OperationLeaseLost

logger = logging.getLogger(__name__)




@asynccontextmanager
async def _borrowed_permit(guard):
    """Acquisition uses a permit already held by this delivery handler."""
    yield guard


async def handle_capture_delivery(message, store: FrontierStore, pipeline, playwright,
                                  *, operation_bucket, domain_bucket) -> None:
    try:
        work = CaptureWork.model_validate_json(message.data)
    except ValueError:
        logger.exception("invalid capture delivery")
        await message.term()
        return
    acquisition = await asyncio.to_thread(store.get_acquisition, work.acquisition_id)
    if (acquisition is None or acquisition.generation != work.generation
            or acquisition.status != "dispatched"):
        await message.ack()
        return
    context = acquisition_context(acquisition)
    current_domain = None

    async def defer(delay, *, reason=None):
        released = await asyncio.to_thread(store.defer_unstarted, work.acquisition_id, work.generation,
                                          delay_seconds=min(86400, max(1, delay)), domain_policy=current_domain if reason is None else None, reason=reason)
        if released:
            await message.ack()
        else:
            await message.nak(delay=5)

    capture = heartbeat = None
    lost_tasks = []
    try:
        async with operation_leases(operation_bucket, [str(work.acquisition_id)],
                                    phase="capture", acquire_timeout=0) as operation:
            try:
                # Publication is asynchronous, but known delivery failure must not
                # spend a physical attempt. This reads existing handles only.
                async with asyncio.timeout(5):
                    await pipeline.queue.check_available()
            except Exception:
                logger.warning("capture waiting for ingestion delivery acquisition=%s", work.acquisition_id)
                await defer(30, reason="ingestion_delivery_unavailable")
                return
            try:
                await pipeline.check_storage_available()
            except Exception:
                await defer(30, reason="storage_unavailable")
                return
            try:
                await public_destination_url(acquisition.url)
            except DestinationUnavailable:
                await defer(30, reason="destination_dns_unavailable")
                return
            except DestinationRejected:
                rejected = await asyncio.to_thread(store.reject_destination, work.acquisition_id, work.generation)
                if rejected:
                    await message.ack()
                else:
                    await message.nak(delay=5)
                return
            current_domain = await asyncio.to_thread(store.current_domain_policy, work.acquisition_id)
            if current_domain.paused:
                await defer(5)
                return
            async with domain_permit(domain_bucket, domain=acquisition.domain,
                                     concurrency=current_domain.maximum_concurrency,
                                     acquire_timeout=0) as domain:
                async with connect_cdp(playwright) as browser:
                    delay = await try_domain_start(domain_bucket, domain=acquisition.domain,
                                                   interval_seconds=current_domain.minimum_request_interval_seconds)
                    if delay > 0:
                        await defer(delay)
                        return
                    started = await asyncio.to_thread(store.begin_attempt, work.acquisition_id, work.generation,
                                                      domain_policy=current_domain)
                    if not started:
                        await defer(5)
                        return
                    # Start freezes the latest exclusions; dispatch may precede an
                    # operator update. Reload that authorized attempt before remote I/O.
                    acquisition = await asyncio.to_thread(store.get_acquisition, work.acquisition_id)
                    context = acquisition_context(acquisition)
                    needs_navigation = await asyncio.to_thread(store.needs_navigation, work.acquisition_id)

                    async def renew():
                        while True:
                            if not await asyncio.to_thread(store.renew_attempt, work.acquisition_id, work.generation):
                                raise StaleDispatch("capture claim heartbeat was rejected")
                            await message.in_progress()
                            await asyncio.sleep(10)

                    heartbeat = asyncio.create_task(renew())
                    lost_tasks = [asyncio.create_task(guard.wait_lost()) for guard in (operation, domain)]
                    capture = asyncio.create_task(acquire_page(
                        url=acquisition.url, context=context, browser=browser,
                        repository_pipeline=pipeline, domain_pacing=domain_bucket,
                        domain_permit=_borrowed_permit(domain), domain_start_reserved=True, include_html=needs_navigation,
                        persist_retryable_failure=acquisition.attempt_count >= acquisition.attempt_limit,
                    ))
                    done, _ = await asyncio.wait([capture, heartbeat, *lost_tasks], return_when=asyncio.FIRST_COMPLETED)
                    if operation.lost or domain.lost:
                        raise OperationLeaseLost("capture lost its operation or domain lease")
                    if heartbeat in done:
                        await heartbeat
                    result = await capture
                    if result.evidence is None:
                        raise ValueError("acquisition returned no frozen observation evidence")
                    navigation = None
                    if needs_navigation and result.html is not None and result.content_sha256 is not None:
                        url = result.evidence.visit.effective_url or acquisition.url
                        payload, rows = await asyncio.to_thread(build_navigation_package, result.html,
                                                                content_sha256=result.content_sha256, page_url=url)
                        # A dispatch-specific immutable object avoids accidental collision
                        # with a later physical retry whose content differs.
                        name = f"runtime/navigation/{acquisition.id.hex}/{sha256(payload).hexdigest()}.arrow"
                        navigation = await asyncio.to_thread(put_navigation_package, pipeline.html_repository.store,
                                                              name=name, payload=payload, row_count=rows)
                    if operation.lost or domain.lost:
                        raise OperationLeaseLost("capture lost ownership before outcome acceptance")
                    await asyncio.to_thread(store.complete, acquisition.id, work.generation,
                                             evidence=result.evidence, navigation=navigation)
        await message.ack()
    except CdpUnavailable:
        await defer(30, reason="cdp_unavailable")
    except DomainCapacityUnavailable:
        await defer(5)
    except RetryableAcquisitionFailure as failure:
        delay = min(3600, max(1, failure.retry_after_seconds or 2 ** acquisition.attempt_count))
        accepted = await asyncio.to_thread(store.defer_retry, acquisition.id, work.generation,
                                           failure.result, delay_seconds=delay)
        if not accepted:
            raise RuntimeError("terminal attempt unexpectedly requested another retry") from failure
        await message.ack()
    except asyncio.CancelledError:
        raise
    except PlaywrightRuntimeLost:
        await message.nak(delay=5)
        raise
    except Exception:
        logger.exception("capture delivery deferred acquisition=%s", work.acquisition_id)
        await message.nak(delay=5)
    finally:
        tasks = [task for task in (capture, heartbeat, *lost_tasks) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
