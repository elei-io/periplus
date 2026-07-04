export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(message: string, status: number, detail: unknown = null) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }
}

export function apiUrl(path: string) {
  const baseUrl = import.meta.env.VITE_API_URL

  if (!baseUrl) {
    return path
  }

  return new URL(path, baseUrl).toString()
}

export function extractApiError(error: unknown) {
  if (error instanceof ApiError) {
    return error.message
  }

  if (error instanceof Error) {
    return error.message
  }

  return "Something went wrong."
}

export async function apiErrorFromResponse(response: Response) {
  const text = await response.text()

  if (!text) {
    return new ApiError(response.statusText, response.status)
  }

  try {
    const body = JSON.parse(text) as unknown
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : body
    const message =
      typeof detail === "string"
        ? detail
        : `Request failed with status ${response.status}.`

    return new ApiError(message, response.status, detail)
  } catch {
    return new ApiError(text, response.status, text)
  }
}
