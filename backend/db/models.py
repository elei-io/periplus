"""Import SQLAlchemy model classes here so Alembic autogenerate can see them."""

import control.crawl_policies.models  # noqa: F401
import control.catalogue_views.models  # noqa: F401
import control.catalogue_materializations.models  # noqa: F401
import control.catalogue_queries.models  # noqa: F401
import control.data_schemas.models  # noqa: F401
import control.query_schemas.models  # noqa: F401
import control.tasks.models  # noqa: F401
import control.url_matching  # noqa: F401
