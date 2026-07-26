"""Import SQLAlchemy model classes here so Alembic autogenerate can see them."""

import control.crawl_policies.models  # noqa: F401
import control.domain_policies.models  # noqa: F401
import control.crawl_graphs.models  # noqa: F401
import control.crawl_schedules.models  # noqa: F401
import runtime.graph_models  # noqa: F401
