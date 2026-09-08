from sqlalchemy.orm import Mapped, mapped_column
from periplus.platform.postgres.base import Base
from periplus.platform.postgres.types import json_type


class PublicAccessRecord(Base):
    __tablename__ = "public_access"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    configuration: Mapped[dict] = mapped_column(json_type)
    # Exactly three current global windows, not an access-history ledger.
    windows: Mapped[dict] = mapped_column(json_type, default=dict)
