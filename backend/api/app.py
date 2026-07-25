import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.catalogue_control import CatalogueControl
from api.graph_runtime import ApiGraphRuntime
from api.graph_submission import frozen_edge_compiler
from api.routers import (
    catalogue,
    catalogue_queries,
    catalogue_scalar_macros,
    catalogue_table_macros,
    catalogue_views,
    crawl_graphs,
    crawl_schedules,
    crawl_policies,
    domain_policies,
    graph_runs,
    catalogue_materializations,
    operational_metrics,
    repository_operations,
    ai,
)
from control.catalogue_materializations.service import (
    unavailable_materialized_views,
)
from repository.catalogue.quack_runtime import (
    CatalogueQueryExecutionError,
    QuackQueryRuntime,
)
from repository.catalogue.compiler_definitions import (
    CatalogueCompilerDefinitionCache,
    read_catalogue_compiler_definitions,
)
from repository.catalogue.query import (
    ClassifiedCatalogueStatement,
    referenced_catalogue_views,
)
from runtime.catalogue_workers import ensure_catalogue_worker_storage
from runtime.catalogue_events import DDL_SUBJECT
from runtime.catalogue_queries import ensure_catalogue_query_storage
from runtime.crawl_scheduler import run_scheduler
from runtime.graph_queue import (
    ensure_graph_storage,
)
from runtime.graph_outbox import run_outbox_relay
from runtime.nats_client import connect_nats


async def _preflight_catalogue_query(
    control: CatalogueControl,
    statement: ClassifiedCatalogueStatement,
) -> None:
    view_names = referenced_catalogue_views(statement)
    if not view_names:
        return
    unavailable = await control.run(
        lambda session, _catalogue: unavailable_materialized_views(
            session, view_names
        )
    )
    if not unavailable:
        return
    details = "; ".join(
        (
            f"views.{name} materialization is {state}"
            + (f": {error}" if error else "")
        )
        for name, state, error in unavailable
    )
    raise CatalogueQueryExecutionError(details[:2_000])


@asynccontextmanager
async def lifespan(app: FastAPI):
    nats_client = await connect_nats()
    quack_runtime = None
    catalogue_control = None
    scheduler_stop = None
    scheduler_task = None
    outbox_stop = None
    outbox_task = None
    ddl_subscription = None
    try:
        jetstream = nats_client.jetstream()
        runs, requests, workers = await ensure_graph_storage(jetstream)
        catalogue_workers = await ensure_catalogue_worker_storage(jetstream)
        graph_runtime = ApiGraphRuntime(
            nats_client=nats_client,
            jetstream=jetstream,
            runs=runs,
            requests=requests,
            workers=workers,
            catalogue_workers=catalogue_workers,
        )
        app.state.graph_runtime = graph_runtime
        outbox_stop = asyncio.Event()
        outbox_task = asyncio.create_task(
            run_outbox_relay(runs, jetstream, stop=outbox_stop),
            name="graph-outbox",
        )
        query_bucket = await ensure_catalogue_query_storage(jetstream)
        catalogue_control = CatalogueControl()
        await catalogue_control.start()
        app.state.catalogue_control = catalogue_control
        quack_runtime = QuackQueryRuntime(
            query_bucket,
            query_preflight=lambda statement: _preflight_catalogue_query(
                catalogue_control, statement
            ),
        )
        await quack_runtime.start()
        app.state.quack_runtime = quack_runtime
        compiler_definitions = CatalogueCompilerDefinitionCache(
            lambda: quack_runtime.run_internal(
                lambda connection: read_catalogue_compiler_definitions(
                    connection,
                    catalogue_alias=quack_runtime.config.catalogue_alias,
                )
            ),
            ttl_seconds=60,
        )
        app.state.compiler_definitions = compiler_definitions

        async def invalidate_compiler_definitions(_message) -> None:
            compiler_definitions.invalidate()

        async def compile_scheduled_edge(sql: str):
            definitions = await compiler_definitions.get()
            return await frozen_edge_compiler(definitions)(sql)

        ddl_subscription = await nats_client.subscribe(
            DDL_SUBJECT,
            cb=invalidate_compiler_definitions,
        )
        scheduler_stop = asyncio.Event()
        scheduler_task = asyncio.create_task(
            run_scheduler(
                scheduler_stop,
                runs=runs,
                requests=requests,
                progress=runs,
                jetstream=jetstream,
                catalogue_snapshot_resolver=catalogue_control.latest_snapshot,
                edge_compiler=compile_scheduled_edge,
            ),
            name="crawl-scheduler",
        )
        yield
    finally:
        if scheduler_stop is not None:
            scheduler_stop.set()
        if outbox_stop is not None:
            outbox_stop.set()
        if scheduler_task is not None:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
        if outbox_task is not None:
            outbox_task.cancel()
            await asyncio.gather(outbox_task, return_exceptions=True)
        if catalogue_control is not None:
            await catalogue_control.close()
        if ddl_subscription is not None:
            await ddl_subscription.unsubscribe()
        if quack_runtime is not None:
            await quack_runtime.close()
        await nats_client.drain()


app = FastAPI(title="Atlas API", lifespan=lifespan)
app.include_router(catalogue.router)
app.include_router(catalogue_queries.router)
app.include_router(catalogue_scalar_macros.router)
app.include_router(catalogue_table_macros.router)
app.include_router(catalogue_views.router)
app.include_router(catalogue_materializations.router)
app.include_router(operational_metrics.router)
app.include_router(repository_operations.router)
app.include_router(ai.router)
app.include_router(crawl_graphs.router)
app.include_router(crawl_schedules.router)
app.include_router(crawl_schedules.resource_router)
app.include_router(graph_runs.trigger_router)
app.include_router(graph_runs.router)
app.include_router(crawl_policies.router)
app.include_router(domain_policies.router)
