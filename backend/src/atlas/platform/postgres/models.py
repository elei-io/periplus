"""Import SQLAlchemy model classes here so Alembic autogenerate can see them."""

import atlas.crawl.control.crawl_graphs.models  # noqa: F401
import atlas.crawl.control.content_policies.models  # noqa: F401
import atlas.crawl.control.crawl_schedules.models  # noqa: F401
import atlas.crawl.control.domain_policies.models  # noqa: F401
import atlas.crawl.runtime.graph_models  # noqa: F401
import atlas.materialization.models  # noqa: F401
