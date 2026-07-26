import assert from "node:assert/strict"
import test from "node:test"

import {
  renderConsoleAssistantHeading,
  renderConsoleResult,
  renderConsoleWelcome,
} from "./console-output.ts"

test("web console tables stay terminal-native and width bounded", () => {
  const output = renderConsoleResult(
    {
      kind: "table",
      columns: ["name", "description"],
      rows: [["elements", "x".repeat(200)]],
      summary: "1 row · 4ms",
    },
    48
  )
  const plain = output
    .replaceAll("\u001b[1;34m", "")
    .replaceAll("\u001b[2m", "")
    .replaceAll("\u001b[0m", "")

  assert.match(plain, /name\s+description/)
  assert.match(plain, /…/)
  assert.match(plain, /1 row · 4ms/)
})

test("web welcome uses shared status versions", () => {
  const welcome = renderConsoleWelcome({
    connection: { state: "connected" },
    lakeSlug: "atlas_test",
    schemaVersion: "v0.2.0",
    compilerVersion: "0.1.0",
    compiler: { state: "idle" },
  })

  assert.match(
    welcome,
    /Lake atlas_test · Atlas schema v0\.2\.0 · compiler 0\.1\.0/
  )
})

test("AI results have distinct progress and answer treatments", () => {
  const progress = renderConsoleResult(
    {
      kind: "progress",
      state: "completed",
      activity: "catalogue",
      label: "Catalogue inspected",
      durationMilliseconds: 174,
    },
    80
  )
  const answer = renderConsoleResult(
    {
      kind: "assistant",
      text: "The lake contains retained book data.",
    },
    80
  )

  assert.match(progress, /✓.*Catalogue inspected.*174ms/)
  assert.match(renderConsoleAssistantHeading(), /◆ Atlas/)
  assert.match(answer, /└ .*The lake contains retained book data\./)
})

test("AI suggestions render metadata without their SQL", () => {
  const output = renderConsoleResult(
    {
      kind: "assistant",
      text: "Two useful ways to inspect this.",
      suggestions: [
        {
          index: 2,
          title: "Price distribution",
          description: "Summarise prices by percentile.",
        },
      ],
    },
    80
  )

  assert.match(output, /2\..*Price distribution/)
  assert.match(output, /\.ai <number> · --copy · --show/)
  assert.doesNotMatch(output, /SELECT/)
})
