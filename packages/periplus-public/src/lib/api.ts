export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string, readonly retryAfterSeconds?: number) { super(message) }
}
export function extractApiError(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong."
}

export async function responseJson<T>(response: Response): Promise<T> {
  const body = await response.json()
  if (!response.ok) {
    const error = typeof body?.detail === "object" ? body.detail : body
    const retry = Number(response.headers.get("retry-after"))
    throw new ApiError(typeof error?.detail === "string" ? error.detail : "Request failed.", response.status, error?.code, Number.isFinite(retry) && retry > 0 ? retry : undefined)
  }
  return body as T
}
