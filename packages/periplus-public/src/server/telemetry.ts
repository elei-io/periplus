// Fixed label vocabulary; never accept URLs, prompts, SQL, user IDs or errors.
type Operation = "assistant" | "query_proxy";
type Outcome =
  | "success"
  | "failed"
  | "cancelled"
  | "timeout"
  | "rejected"
  | "unconfigured"
  | "invalid";
const buckets = [0.1, 0.5, 1, 5, 15, 30, 60, 120, 180, 300];
type Series = { count: number; seconds: number; buckets: number[] };
type State = {
  active: Record<Operation, number>;
  outcomes: Map<string, Series>;
  tokens: { input: number; output: number };
};
const globalTelemetry = globalThis as typeof globalThis & {
  periplusTelemetry?: State;
};
const state = (globalTelemetry.periplusTelemetry ??= {
  active: { assistant: 0, query_proxy: 0 },
  outcomes: new Map(),
  tokens: { input: 0, output: 0 },
});

export function beginOperation(operation: Operation) {
  const id = crypto.randomUUID(),
    started = performance.now();
  state.active[operation]++;
  let finished = false;
  return (outcome: Outcome) => {
    if (finished) return;
    finished = true;
    state.active[operation]--;
    const seconds = (performance.now() - started) / 1000;
    const key = `${operation}:${outcome}`;
    const series = state.outcomes.get(key) ?? {
      count: 0,
      seconds: 0,
      buckets: buckets.map(() => 0),
    };
    series.count++;
    series.seconds += seconds;
    buckets.forEach((bound, i) => {
      if (seconds <= bound) series.buckets[i]++;
    });
    state.outcomes.set(key, series);
    console.info(
      JSON.stringify({
        event: "operation_finished",
        time: new Date().toISOString(),
        service: "public",
        operation,
        operation_id: id,
        outcome,
        elapsed_ms: seconds * 1000,
      }),
    );
  };
}

export function recordTokens(
  input: number | undefined,
  output: number | undefined,
) {
  if (input !== undefined && Number.isFinite(input))
    state.tokens.input += Math.max(0, input);
  if (output !== undefined && Number.isFinite(output))
    state.tokens.output += Math.max(0, output);
}

export function prometheusMetrics() {
  const lines = [
    "# HELP periplus_public_active_operations Current operations in this public process.",
    "# TYPE periplus_public_active_operations gauge",
    "# HELP periplus_public_operations_total Terminal public operation outcomes.",
    "# TYPE periplus_public_operations_total counter",
    "# HELP periplus_public_operation_duration_seconds End to end public operation duration.",
    "# TYPE periplus_public_operation_duration_seconds histogram",
    "# HELP periplus_public_model_tokens_total Provider reported model tokens.",
    "# TYPE periplus_public_model_tokens_total counter",
  ];
  for (const [operation, active] of Object.entries(state.active))
    lines.push(
      `periplus_public_active_operations{operation="${operation}"} ${active}`,
    );
  for (const [key, series] of state.outcomes) {
    const [operation, outcome] = key.split(":");
    const labels = `operation="${operation}",outcome="${outcome}"`;
    lines.push(`periplus_public_operations_total{${labels}} ${series.count}`);
    buckets.forEach((bound, i) =>
      lines.push(
        `periplus_public_operation_duration_seconds_bucket{${labels},le="${bound}"} ${series.buckets[i]}`,
      ),
    );
    lines.push(
      `periplus_public_operation_duration_seconds_bucket{${labels},le="+Inf"} ${series.count}`,
      `periplus_public_operation_duration_seconds_count{${labels}} ${series.count}`,
      `periplus_public_operation_duration_seconds_sum{${labels}} ${series.seconds}`,
    );
  }
  for (const [direction, value] of Object.entries(state.tokens))
    lines.push(
      `periplus_public_model_tokens_total{direction="${direction}"} ${value}`,
    );
  return lines.join("\n") + "\n";
}
