import type { SqlAssistantEvent, SqlAssistantReply } from "../types/sql-assistant"

export async function readAssistantStream(response: Response, onEvent: (event: SqlAssistantEvent) => void): Promise<SqlAssistantReply> {
  if (!response.body) throw new Error("The assistant response was empty.")
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ""
  let reply: SqlAssistantReply | undefined
  const consume = (line: string) => {
    if (!line.trim()) return
    const event: SqlAssistantEvent = JSON.parse(line)
    if (event.type === "error") throw new Error(event.message)
    if (event.type === "result") reply = event.reply
    onEvent(event)
  }
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value
      let end: number
      while ((end = buffer.indexOf("\n")) >= 0) {
        consume(buffer.slice(0, end))
        buffer = buffer.slice(end + 1)
      }
    }
    consume(buffer)
    if (!reply) throw new Error("The assistant connection ended before a response arrived. Please try again.")
    return reply
  } finally {
    await reader.cancel()
    reader.releaseLock()
  }
}
