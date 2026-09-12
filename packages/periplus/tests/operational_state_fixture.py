"""Disposable operational database; no test touches configured control state."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch
from periplus.platform.postgres import session
from periplus.materialization.models import MaterializationStateRecord, MaterializationAppliedBatchRecord, MaterializationRunRecord, MaterializationBatchRecord
from periplus.retention.models import LakeWriteClaimRecord, RetiredEvidenceRecord, RetentionObjectRecord


def operational_state(test):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    for model in (MaterializationStateRecord, MaterializationAppliedBatchRecord, MaterializationRunRecord, MaterializationBatchRecord,
                  LakeWriteClaimRecord, RetiredEvidenceRecord, RetentionObjectRecord):
        model.__table__.create(engine)
    test.addCleanup(engine.dispose)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    replacement = patch.object(session, 'SessionLocal', factory)
    replacement.start()
    test.addCleanup(replacement.stop)
    return factory
