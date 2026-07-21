"""Import SQLAlchemy model classes here so Alembic autogenerate can see them."""

import control.crawl_policies.models  # noqa: F401
import control.domain_policies.models  # noqa: F401
import control.crawl_graphs.models  # noqa: F401
import control.crawl_schedules.models  # noqa: F401
import control.chats.models  # noqa: F401
import control.catalogue_views.models  # noqa: F401
import control.catalogue_materializations.models  # noqa: F401
import control.catalogue_queries.models  # noqa: F401
import control.catalogue_table_macros.models  # noqa: F401
import control.catalogue_scalar_macros.models  # noqa: F401
