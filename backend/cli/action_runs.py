from typing import Any

from pydantic import TypeAdapter

from actions.shared.progress import CrawlProgressCallback
from db.session import session_scope
from tasks.schemas import TaskPrimitive
from tasks.service import execute_ad_hoc_task_run_sync


def run_action(
    primitive: TaskPrimitive,
    input_value: dict[str, Any],
    response_adapter: TypeAdapter,
    progress_callback: CrawlProgressCallback | None = None,
) -> Any:
    with session_scope() as session:
        response = execute_ad_hoc_task_run_sync(
            session=session,
            primitive=primitive,
            input_value=input_value,
            progress_callback=progress_callback,
        )
        return response_adapter.validate_python(response)
