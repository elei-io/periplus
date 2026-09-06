import { z } from "zod"

// Never accept client-supplied tools, tool results, system messages, or attachments as evidence.
const incoming = z.object({ messages: z.array(z.object({
  id: z.string().max(100), role: z.enum(["user", "assistant"]),
  parts: z.array(z.unknown()).max(100),
})).min(1).max(16) })
export function assistantMessages(value: unknown) {
  const { messages } = incoming.parse(value)
  if (messages.at(-1)?.role !== "user") throw new Error("A user question is required.")
  return messages.map(message => {
    const text = message.parts.flatMap(part => {
      const parsed = z.object({ type: z.literal("text"), text: z.string().max(8000) }).safeParse(part)
      return parsed.success ? [parsed.data] : []
    })
    if (!text.length || text.reduce((n, part) => n + part.text.length, 0) > 8000) throw new Error("Message is empty or too long.")
    return { ...message, parts: text }
  })
}
