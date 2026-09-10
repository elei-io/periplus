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

test("client operation IDs correlate logs without accepting arbitrary input", () => {
  const messages: string[] = [];
  const original = console.info;
  console.info = (message: string) => { messages.push(message); };
  try {
    const id = "a8da4c02-9c62-4fde-9997-d35c2c2a05c4";
    beginOperation("query_proxy", id)("success");
    beginOperation("query_proxy", "SQL secret\nforged log")("rejected");
    assert.equal(JSON.parse(messages[0]).operation_id, id);
    assert.match(JSON.parse(messages[1]).operation_id, /^[0-9a-f-]{36}$/);
    assert.doesNotMatch(messages.join(""), /SQL secret|forged log/);
  } finally { console.info = original; }
});
