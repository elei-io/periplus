"""Continuous crawler control-plane baseline; reset disposable prior state before installing."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260907_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('collections',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('spec', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('retiring', sa.Boolean(), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('scheduling_turn', sa.Integer(), nullable=False),
    sa.Column('page_limit', sa.Integer(), nullable=False),
    sa.Column('reserved', sa.Integer(), nullable=False),
    sa.Column('consumed', sa.Integer(), nullable=False),
    sa.Column('seeds_settled', sa.Boolean(), nullable=False),
    sa.Column('seed_provenance', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('discovery_state', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('selection_checkpoint', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('waiting_reason', sa.Text(), nullable=True),
    sa.Column('service_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('service_token', sa.Uuid(), nullable=True),
    sa.Column('service_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('service_failures', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_dispatch_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('active', 'paused', 'settled')", name='ck_collection_status'),
    sa.CheckConstraint('page_limit > 0 AND reserved >= 0 AND consumed >= 0 AND reserved + consumed <= page_limit', name='ck_collection_budget'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_collection_service', 'collections', ['status', 'service_after', 'service_expires_at'], unique=False)
    op.create_table('content_policies',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('scheme', sa.Text(), nullable=False),
    sa.Column('host', sa.Text(), nullable=False),
    sa.Column('path_prefix', sa.Text(), nullable=False),
    sa.Column('path_mode', sa.Text(), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("path_mode IN ('exact', 'prefix')", name='ck_content_policies_path_mode'),
    sa.CheckConstraint("scheme IN ('*', 'http', 'https')", name='ck_content_policies_scheme'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('scheme', 'host', 'path_prefix', 'path_mode', name='uq_content_policies_match'),
    sa.UniqueConstraint('slug')
    )
    op.create_index('ix_content_policies_enabled', 'content_policies', ['enabled'], unique=False)
    op.create_index('ix_content_policies_host', 'content_policies', ['host'], unique=False)
    op.create_table('domain_policies',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('host_match', sa.Text(), nullable=False),
    sa.Column('maximum_concurrency', sa.Integer(), nullable=False),
    sa.Column('minimum_request_interval_seconds', sa.Float(), nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated_by', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('maximum_concurrency >= 1', name='ck_domain_policies_concurrency'),
    sa.CheckConstraint('minimum_request_interval_seconds >= 0', name='ck_domain_policies_interval'),
    sa.CheckConstraint('version >= 1', name='ck_domain_policies_version'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('host_match'),
    sa.UniqueConstraint('slug')
    )
    op.create_index('ix_domain_policies_enabled', 'domain_policies', ['enabled'], unique=False)
    op.create_table('frontier_acquisitions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('domain', sa.Text(), nullable=False),
    sa.Column('capture_key', sa.Text(), nullable=False),
    sa.Column('pending_key', sa.Text(), nullable=True),
    sa.Column('requirements', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('visibility', sa.Text(), nullable=False),
    sa.Column('access_context', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('dispatch_policy_version', sa.Integer(), nullable=True),
    sa.Column('eligible_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('defer_reason', sa.Text(), nullable=True),
    sa.Column('domain_eligible_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('domain_policy_id', sa.Uuid(), nullable=True),
    sa.Column('domain_policy_version', sa.Integer(), nullable=True),
    sa.Column('claim_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempt_domain_policy', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('attempt_exclusions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('attempt_exclusion_version', sa.Integer(), nullable=False),
    sa.Column('attempt_background', sa.Boolean(), nullable=False),
    sa.Column('attempt_reserved_ms', sa.Integer(), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('attempt_limit', sa.Integer(), nullable=False),
    sa.Column('attempt_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('uncertain_attempts', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('prior_results', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('background_url_key', sa.Text(), nullable=True),
    sa.Column('background_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('background_token', sa.Uuid(), nullable=True),
    sa.Column('background_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('background_failures', sa.Integer(), nullable=False),
    sa.Column('terminal_reason', sa.Text(), nullable=True),
    sa.Column('background_error', sa.Text(), nullable=True),
    sa.Column('background_selected', sa.Boolean(), nullable=False),
    sa.Column('background_reason', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('frozen_reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('outcome', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('navigation', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('retired_navigation', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('evidence_snapshot', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'retry', 'dispatched', 'succeeded', 'failed', 'cancelled')", name='ck_frontier_acquisition_status'),
    sa.CheckConstraint('attempt_count >= 0 AND attempt_limit > 0 AND attempt_count <= attempt_limit', name='ck_frontier_attempt_limit'),
    sa.CheckConstraint('generation >= 0', name='ck_frontier_generation'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('background_url_key', name='uq_frontier_background_active_url'),
    sa.UniqueConstraint('pending_key', name='uq_frontier_pending_capture')
    )
    op.create_index(op.f('ix_frontier_acquisitions_domain'), 'frontier_acquisitions', ['domain'], unique=False)
    op.create_index('ix_frontier_background_parent', 'frontier_acquisitions', ['visibility', 'status', 'background_selected', 'background_after', 'created_at'], unique=False)
    op.create_index('ix_frontier_eligible', 'frontier_acquisitions', ['status', 'eligible_at', 'created_at'], unique=False)
    op.create_index('ix_frontier_recent', 'frontier_acquisitions', ['capture_key', 'status', 'completed_at'], unique=False)
    op.create_table('frontier_control',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('exclusions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('exclusion_cursor', sa.Uuid(), nullable=True),
    sa.Column('retention_cursor', sa.Uuid(), nullable=True),
    sa.Column('collection_retention_cursor', sa.Uuid(), nullable=True),
    sa.Column('collection_limit', sa.Integer(), nullable=False),
    sa.Column('interest_limit', sa.Integer(), nullable=False),
    sa.Column('interest_count', sa.Integer(), nullable=False),
    sa.Column('acquisition_limit', sa.Integer(), nullable=False),
    sa.Column('admission_limit', sa.Integer(), nullable=False),
    sa.Column('dispatch_limit', sa.Integer(), nullable=False),
    sa.Column('pending_count', sa.Integer(), nullable=False),
    sa.Column('active_count', sa.Integer(), nullable=False),
    sa.Column('captures_per_minute', sa.Integer(), nullable=False),
    sa.Column('next_dispatch_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('background_share', sa.Integer(), nullable=False),
    sa.Column('background_credit', sa.Integer(), nullable=False),
    sa.Column('background_attempt_allowance', sa.BigInteger(), nullable=False),
    sa.Column('background_capture_time_allowance_ms', sa.BigInteger(), nullable=False),
    sa.Column('background_reserved_attempts', sa.BigInteger(), nullable=False),
    sa.Column('background_started_attempts', sa.BigInteger(), nullable=False),
    sa.Column('background_reserved_capture_ms', sa.BigInteger(), nullable=False),
    sa.Column('background_charged_capture_ms', sa.BigInteger(), nullable=False),
    sa.Column('attempt_allowance', sa.BigInteger(), nullable=False),
    sa.Column('capture_time_allowance_ms', sa.BigInteger(), nullable=False),
    sa.Column('capture_timeout_ms', sa.Integer(), nullable=False),
    sa.Column('reserved_attempts', sa.BigInteger(), nullable=False),
    sa.Column('started_attempts', sa.BigInteger(), nullable=False),
    sa.Column('reserved_capture_ms', sa.BigInteger(), nullable=False),
    sa.Column('charged_capture_ms', sa.BigInteger(), nullable=False),
    sa.Column('policy_version', sa.Integer(), nullable=False),
    sa.Column('scheduling_turn', sa.Integer(), nullable=False),
    sa.Column('last_dispatch_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.Text(), nullable=True),
    sa.CheckConstraint('acquisition_limit > 0 AND admission_limit > 0 AND dispatch_limit > 0 AND collection_limit > 0 AND interest_limit > 0', name='ck_frontier_limits'),
    sa.CheckConstraint('attempt_allowance >= 0 AND capture_time_allowance_ms >= 0 AND capture_timeout_ms BETWEEN 1000 AND 3600000', name='ck_frontier_attempt_allowances'),
    sa.CheckConstraint('background_attempt_allowance >= 0 AND background_capture_time_allowance_ms >= 0 AND background_credit BETWEEN 0 AND 99', name='ck_frontier_background_allowances'),
    sa.CheckConstraint('background_reserved_attempts >= 0 AND background_started_attempts >= 0 AND background_reserved_capture_ms >= 0 AND background_charged_capture_ms >= 0', name='ck_frontier_background_counters'),
    sa.CheckConstraint('captures_per_minute >= 0 AND background_share BETWEEN 0 AND 99', name='ck_frontier_rates'),
    sa.CheckConstraint('id = 1', name='ck_frontier_single_control'),
    sa.CheckConstraint('pending_count >= 0 AND active_count >= 0 AND interest_count >= 0', name='ck_frontier_counts'),
    sa.CheckConstraint('reserved_attempts >= 0 AND started_attempts >= 0 AND reserved_capture_ms >= 0 AND charged_capture_ms >= 0', name='ck_frontier_attempt_counters'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('materialization_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('source_snapshot', sa.BigInteger(), nullable=False),
    sa.Column('covered_snapshot', sa.BigInteger(), nullable=False),
    sa.Column('activation_snapshot', sa.BigInteger(), nullable=True),
    sa.Column('generation_tables', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('registry_digest', sa.Text(), nullable=False),
    sa.Column('batch_size', sa.Integer(), nullable=False),
    sa.Column('total_batches', sa.Integer(), nullable=False),
    sa.Column('completed_batches', sa.Integer(), nullable=False),
    sa.Column('source_items', sa.BigInteger(), nullable=False),
    sa.Column('source_bytes', sa.BigInteger(), nullable=False),
    sa.Column('output_rows', sa.BigInteger(), nullable=False),
    sa.Column('output_bytes', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('plan_published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('activation_published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cleanup_completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'planning', 'running', 'activating', 'completed', 'failed')", name='ck_materialization_runs_status'),
    sa.CheckConstraint('batch_size >= 1', name='ck_materialization_runs_batch_size'),
    sa.CheckConstraint('source_items >= 0 AND source_bytes >= 0 AND output_rows >= 0 AND output_bytes >= 0', name='ck_materialization_runs_counts'),
    sa.CheckConstraint('total_batches >= 0 AND completed_batches >= 0 AND completed_batches <= total_batches', name='ck_materialization_runs_batches'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_materialization_runs_status'), 'materialization_runs', ['status'], unique=False)
    op.create_table('frontier_background_checks',
    sa.Column('parent_observation_id', sa.Uuid(), nullable=False),
    sa.Column('token', sa.Uuid(), nullable=False),
    sa.Column('candidates', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('policy_version', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('snapshot', sa.BigInteger(), nullable=True),
    sa.Column('decisions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['parent_observation_id'], ['frontier_acquisitions.id'], ),
    sa.PrimaryKeyConstraint('parent_observation_id')
    )
    op.create_index('ix_frontier_background_check_expiry', 'frontier_background_checks', ['expires_at'], unique=False)
    op.create_table('frontier_interests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('collection_id', sa.Uuid(), nullable=False),
    sa.Column('acquisition_id', sa.Uuid(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('url_key', sa.Text(), nullable=False),
    sa.Column('context', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('mode', sa.Text(), nullable=False),
    sa.Column('budget_state', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('selection_checkpoint', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("budget_state IN ('reserved', 'consumed', 'released')", name='ck_frontier_interest_budget'),
    sa.CheckConstraint("status IN ('queued', 'awaiting_result', 'selecting', 'settled', 'cancelled')", name='ck_frontier_interest_status'),
    sa.ForeignKeyConstraint(['acquisition_id'], ['frontier_acquisitions.id'], ),
    sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('collection_id', 'url_key', name='uq_frontier_collection_url_key')
    )
    op.create_index(op.f('ix_frontier_interests_acquisition_id'), 'frontier_interests', ['acquisition_id'], unique=False)
    op.create_index(op.f('ix_frontier_interests_collection_id'), 'frontier_interests', ['collection_id'], unique=False)
    op.create_index('ix_frontier_selection', 'frontier_interests', ['collection_id', 'status', 'created_at'], unique=False)
    op.create_table('frontier_outbox',
    sa.Column('message_id', sa.Text(), nullable=False),
    sa.Column('acquisition_id', sa.Uuid(), nullable=True),
    sa.Column('collection_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('committed_snapshot', sa.BigInteger(), nullable=True),
    sa.Column('committed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_receipt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('receipt_checks', sa.Integer(), nullable=False),
    sa.Column('claim_token', sa.Uuid(), nullable=True),
    sa.Column('claim_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('not_before', sa.DateTime(timezone=True), nullable=True),
    sa.Column('publish_attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.CheckConstraint('publish_attempts >= 0', name='ck_frontier_publish_attempts'),
    sa.CheckConstraint('receipt_checks >= 0', name='ck_frontier_receipt_checks'),
    sa.ForeignKeyConstraint(['acquisition_id'], ['frontier_acquisitions.id'], ),
    sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], ),
    sa.PrimaryKeyConstraint('message_id')
    )
    op.create_index('ix_frontier_ingestion_receipts', 'frontier_outbox', ['kind', 'committed_snapshot', 'next_receipt_at', 'published_at'], unique=False)
    op.create_index(op.f('ix_frontier_outbox_acquisition_id'), 'frontier_outbox', ['acquisition_id'], unique=False)
    op.create_index(op.f('ix_frontier_outbox_collection_id'), 'frontier_outbox', ['collection_id'], unique=False)
    op.create_index('ix_frontier_outbox_ready', 'frontier_outbox', ['published_at', 'not_before', 'claim_expires_at'], unique=False)
    op.create_table('materialization_batches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.BigInteger(), nullable=False),
    sa.Column('visit_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('source_items', sa.BigInteger(), nullable=False),
    sa.Column('source_bytes', sa.BigInteger(), nullable=False),
    sa.Column('output_rows', sa.BigInteger(), nullable=False),
    sa.Column('output_bytes', sa.BigInteger(), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'running', 'completed')", name='ck_materialization_batches_status'),
    sa.CheckConstraint('ordinal >= 0 AND attempts >= 0', name='ck_materialization_batches_counters'),
    sa.ForeignKeyConstraint(['run_id'], ['materialization_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'ordinal', name='uq_materialization_batches_run_ordinal')
    )
    op.create_index(op.f('ix_materialization_batches_run_id'), 'materialization_batches', ['run_id'], unique=False)
    op.create_index(op.f('ix_materialization_batches_status'), 'materialization_batches', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_materialization_batches_status'), table_name='materialization_batches')
    op.drop_index(op.f('ix_materialization_batches_run_id'), table_name='materialization_batches')
    op.drop_table('materialization_batches')
    op.drop_index('ix_frontier_outbox_ready', table_name='frontier_outbox')
    op.drop_index(op.f('ix_frontier_outbox_collection_id'), table_name='frontier_outbox')
    op.drop_index(op.f('ix_frontier_outbox_acquisition_id'), table_name='frontier_outbox')
    op.drop_index('ix_frontier_ingestion_receipts', table_name='frontier_outbox')
    op.drop_table('frontier_outbox')
    op.drop_index('ix_frontier_selection', table_name='frontier_interests')
    op.drop_index(op.f('ix_frontier_interests_collection_id'), table_name='frontier_interests')
    op.drop_index(op.f('ix_frontier_interests_acquisition_id'), table_name='frontier_interests')
    op.drop_table('frontier_interests')
    op.drop_index('ix_frontier_background_check_expiry', table_name='frontier_background_checks')
    op.drop_table('frontier_background_checks')
    op.drop_index(op.f('ix_materialization_runs_status'), table_name='materialization_runs')
    op.drop_table('materialization_runs')
    op.drop_table('frontier_control')
    op.drop_index('ix_frontier_recent', table_name='frontier_acquisitions')
    op.drop_index('ix_frontier_eligible', table_name='frontier_acquisitions')
    op.drop_index('ix_frontier_background_parent', table_name='frontier_acquisitions')
    op.drop_index(op.f('ix_frontier_acquisitions_domain'), table_name='frontier_acquisitions')
    op.drop_table('frontier_acquisitions')
    op.drop_index('ix_domain_policies_enabled', table_name='domain_policies')
    op.drop_table('domain_policies')
    op.drop_index('ix_content_policies_host', table_name='content_policies')
    op.drop_index('ix_content_policies_enabled', table_name='content_policies')
    op.drop_table('content_policies')
    op.drop_index('ix_collection_service', table_name='collections')
    op.drop_table('collections')
