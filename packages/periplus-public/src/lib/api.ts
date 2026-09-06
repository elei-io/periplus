export function extractApiError(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong."
}

export async function responseJson<T>(response: Response): Promise<T> {
  const body = await response.json()
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "Request failed.")
  return body as T
}
