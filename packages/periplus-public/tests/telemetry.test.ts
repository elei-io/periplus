import test from "node:test";
import assert from "node:assert/strict";
import {
  beginOperation,
  prometheusMetrics,
  recordTokens,
} from "../src/server/telemetry.ts";

test("terminal telemetry is recorded once and exposes bounded Prometheus series", () => {
  const finish = beginOperation("assistant");
  assert.match(
    prometheusMetrics(),
    /periplus_public_active_operations\{operation="assistant"\} 1/,
  );
  finish("failed");
  finish("success");
  const metrics = prometheusMetrics();
  assert.match(
    metrics,
    /periplus_public_active_operations\{operation="assistant"\} 0/,
  );
  assert.match(
    metrics,
    /periplus_public_operations_total\{operation="assistant",outcome="failed"\} 1/,
  );
  assert.doesNotMatch(metrics, /outcome="success"/);
  recordTokens(10, undefined);
  assert.match(prometheusMetrics(), /direction="input"\} 10/);
});
