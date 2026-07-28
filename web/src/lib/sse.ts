export type SseMessage = {
  event: string
  data: string
  id?: string
}

export function parseSseMessage(rawMessage: string): SseMessage | null {
  const lines = rawMessage.split(/\r?\n/)
  let event = "message"
  let id: string | undefined
  const dataLines: string[] = []

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim()
      continue
    }

    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart())
    }
    if (line.startsWith("id:")) {
      id = line.slice("id:".length).trim()
    }
  }

  if (dataLines.length === 0) {
    return null
  }

  return {
    event,
    data: dataLines.join("\n"),
    id,
  }
}

export async function readSseStream(
  response: Response,
  onMessage: (message: SseMessage) => void
) {
  if (!response.body) {
    throw new Error("The stream did not include a response body.")
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { done, value } = await reader.read()

    if (done) {
      break
    }

    buffer += decoder.decode(value, { stream: true })
    const messages = buffer.split(/\n\n/)
    buffer = messages.pop() ?? ""

    for (const message of messages) {
      const parsedMessage = parseSseMessage(message)

      if (parsedMessage) {
        onMessage(parsedMessage)
      }
    }
  }

  buffer += decoder.decode()

  if (!buffer.trim()) {
    return
  }

  const parsedMessage = parseSseMessage(buffer)

  if (parsedMessage) {
    onMessage(parsedMessage)
  }
}
