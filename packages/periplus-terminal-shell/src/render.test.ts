import assert from "node:assert/strict"
import test from "node:test"

import { renderConsoleResult } from "./render.js"

test("surrounds tables with a blank line", () => {
  const output = renderConsoleResult({
    kind: "table",
    columns: ["name"],
    rows: [["pages"]],
    summary: "1 public object",
  })

  assert(output.startsWith("\nname\n"))
  assert(output.endsWith("1 public object\n\n"))
})

test("surrounds command messages without changing clear", () => {
  assert.equal(
    renderConsoleResult({ kind: "message", text: "Reloaded." }),
    "\nReloaded.\n\n",
  )
  assert.equal(
    renderConsoleResult({ kind: "clear" }),
    "\u001b[2J\u001b[H",
  )
})
