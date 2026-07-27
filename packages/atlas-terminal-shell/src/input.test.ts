import assert from "node:assert/strict"
import test from "node:test"
import { MultilineInput } from "./input.js"

const wait = (milliseconds: number) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds))

test("waits for the final chunk of a multiline SQL paste", async () => {
  const submissions: string[] = []
  const input = new MultilineInput((value) => submissions.push(value), 5)

  input.push("SELECT")
  input.push("  requested_url,")
  input.push("  status_code")
  await wait(15)

  assert.deepEqual(submissions, [])

  input.push("FROM ingest.visits;")
  await wait(15)

  assert.deepEqual(submissions, [
    "SELECT\n  requested_url,\n  status_code\nFROM ingest.visits;",
  ])
})

test("submits ordinary entered lines independently", async () => {
  const submissions: string[] = []
  const input = new MultilineInput((value) => submissions.push(value), 5)

  input.push("SELECT 1;")
  await wait(15)
  input.push("SELECT 2;")
  await wait(15)

  assert.deepEqual(submissions, ["SELECT 1;", "SELECT 2;"])
})

test("clear discards a pending paste", async () => {
  const submissions: string[] = []
  const input = new MultilineInput((value) => submissions.push(value), 5)

  input.push("SELECT")
  input.clear()
  await wait(15)

  assert.deepEqual(submissions, [])
})
