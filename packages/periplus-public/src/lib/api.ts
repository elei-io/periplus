export class ApiError extends Error {
  readonly status: number
  readonly code?: string
  readonly retryAfterSeconds?: number
  constructor(message: string, status: number, code?: string, retryAfterSeconds?: number) {
    super(message)
    this.status = status
    this.code = code
    this.retryAfterSeconds = retryAfterSeconds
  }
}
export function extractApiError(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong."
}

export async function responseJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => {
    throw new ApiError(
      response.ok ? "The server returned an invalid response. Please try again." :
        `Request failed (HTTP ${response.status}). Please try again.`,
      response.status,
    )
  })
  if (!response.ok) {
    const error = typeof body?.detail === "object" ? body.detail : body
    const retry = Number(response.headers.get("retry-after"))
    throw new ApiError(typeof error?.detail === "string" ? error.detail : "Request failed.", response.status, error?.code, Number.isFinite(retry) && retry > 0 ? retry : undefined)
  }
  return body as T
}
