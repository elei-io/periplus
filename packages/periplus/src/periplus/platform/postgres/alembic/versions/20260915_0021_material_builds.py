"""Target, range and publication control for ClickHouse rebuilds."""
from alembic import op
from periplus.materialization.rebuilds.models import BuildRecord, RangeRecord, PublicationRecord
revision = '20260915_0021'
down_revision = '20260915_0020'
branch_labels = None
depends_on = None


def upgrade():
    for model in (BuildRecord, RangeRecord, PublicationRecord):
        model.__table__.create(op.get_bind())


def downgrade():
    for model in (PublicationRecord, RangeRecord, BuildRecord):
        model.__table__.drop(op.get_bind())
