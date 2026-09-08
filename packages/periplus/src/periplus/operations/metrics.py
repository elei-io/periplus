"""Bounded process-local measurements of existing operational work."""
from prometheus_client import Counter, Histogram, Gauge
from periplus.platform.telemetry import DURATION_BUCKETS
capture_outcomes = Counter('periplus_capture_attempts_total', 'Physical capture responses observed, not durable history.', ('outcome', 'status_class'))
capture_duration = Histogram('periplus_capture_duration_seconds', 'Physical capture response duration.', buckets=DURATION_BUCKETS)
capture_throttled = Counter('periplus_capture_throttled_total', 'Observed HTTP 429 responses.')
work_deferred = Counter('periplus_capture_deferred_total', 'Capture delivery deferrals.', ('reason',))
schedule_ticks = Counter('periplus_schedule_ticks_total', 'Committed schedule tick decisions.', ('outcome',))
schedule_lateness = Histogram('periplus_schedule_lateness_seconds', 'Time between due time and observed tick.', buckets=DURATION_BUCKETS)
janitor_passes = Counter('periplus_janitor_passes_total', 'Janitor phase outcomes.', ('phase', 'outcome'))
janitor_last_success = Gauge('periplus_janitor_last_success_timestamp_seconds', 'Last successful janitor phase.', ('phase',))
janitor_duration = Histogram('periplus_janitor_duration_seconds', 'Janitor sweep duration.', buckets=DURATION_BUCKETS)
raw_removed = Counter('periplus_retired_raw_objects_removed_total', 'Retired raw objects removed.')
