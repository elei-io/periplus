"""Public destination preflight; the CDP service still owns network egress enforcement."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from periplus.urls import normalize_url


class DestinationUnavailable(RuntimeError):
    def __init__(self, message: str, *, reason: str = "destination_dns_unavailable"):
        super().__init__(message)
        self.reason = reason


class DestinationRejected(ValueError):
    def __init__(self, message: str, *, reason: str = "non_public_destination"):
        super().__init__(message)
        self.reason = reason


# A timed-out caller cannot stop the OS resolver thread. Keep its lookup charged
# to a bounded per-loop set until it actually finishes, instead of queuing more.
_lookups = WeakKeyDictionary()


async def _resolve(host: str):
    loop = asyncio.get_running_loop()
    active = _lookups.setdefault(loop, set())
    if len(active) >= 4:
        raise DestinationUnavailable("Destination DNS capacity is busy.", reason="destination_dns_capacity")
    task = asyncio.create_task(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM))
    active.add(task)
    def finished(completed):
        active.discard(completed)
        if not completed.cancelled():
            completed.exception()
    task.add_done_callback(finished)
    return await asyncio.wait_for(asyncio.shield(task), 10)


async def public_destination_url(value: str) -> str:
    try:
        url = normalize_url(value)
    except ValueError as exc:
        raise DestinationRejected("Invalid public destination.", reason="invalid_destination") from exc
    host = urlsplit(url).hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        try:
            resolved = await _resolve(host)
        except socket.gaierror as exc:
            negative_results = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}
            reason = ("destination_dns_not_found" if exc.errno in negative_results
                      else "destination_dns_unavailable")
            raise DestinationUnavailable("Destination DNS is unavailable.", reason=reason) from exc
        except TimeoutError as exc:
            raise DestinationUnavailable("Destination DNS timed out.", reason="destination_dns_timeout") from exc
        except OSError as exc:
            raise DestinationUnavailable("Destination DNS is unavailable.") from exc
        addresses = [ipaddress.ip_address(item[4][0]) for item in resolved]
    if not addresses or any(not address.is_global or address.is_multicast or address.is_reserved for address in addresses):
        raise DestinationRejected("Destination must resolve only to public internet addresses.")
    return url
