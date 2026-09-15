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
  const apiPath = path.startsWith("/") ? path : `/${path}`
  return `/api${apiPath}`
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
    const explanation = detail && typeof detail === "object" && "detail" in detail ? (detail as {detail: unknown}).detail : detail
    const message =
      typeof explanation === "string"
        ? explanation
        : `Request failed with status ${response.status}.`

    return new ApiError(message, response.status, detail)
  } catch {
    const message = [502, 503, 504].includes(response.status)
      ? `The API is temporarily unavailable (HTTP ${response.status}). Please retry shortly.`
      : `Request failed with status ${response.status}. The server returned an unexpected response.`
    return new ApiError(message, response.status)
  }
}
