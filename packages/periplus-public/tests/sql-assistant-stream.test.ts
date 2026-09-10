import assert from "node:assert/strict"
import test from "node:test"
import { readAssistantStream } from "../src/lib/sql-assistant-stream.ts"

function response(text: string) {
  const bytes = new TextEncoder().encode(text)
  return new Response(new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
    controller.close()
  } }))
}

test("streams activity across split UTF-8 characters before the final reply", async () => {
  const events: string[] = []
  const reply = { message: "Found café", sql: null, parameters: "[]", validation: null }
  const result = await readAssistantStream(response(JSON.stringify({ type: "activity", activity: { id: "1", label: "café", status: "running" } }) + "\n" + JSON.stringify({ type: "result", reply })), event => events.push(event.type))
  assert.deepEqual(result, reply)
  assert.deepEqual(events, ["activity", "result"])
})

test("surfaces stream failures and incomplete responses", async () => {
  await assert.rejects(readAssistantStream(response('{"type":"error","message":"Service unavailable"}\n'), () => {}), /Service unavailable/)
  await assert.rejects(readAssistantStream(response(""), () => {}), /connection ended/)
})
