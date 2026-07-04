import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import type {
  TaskCreate,
  TaskFilters,
  TaskRecord,
  TaskUpdate,
} from "@/types/tasks"

export async function listTasks(filters: TaskFilters = {}) {
  const params = new URLSearchParams()
  if (filters.primitive) {
    params.set("primitive", filters.primitive)
  }
  if (typeof filters.archived === "boolean") {
    params.set("archived", String(filters.archived))
  }
  if (filters.origin) {
    params.set("origin", filters.origin)
  }

  const query = params.toString()
  const response = await fetch(apiUrl(`/tasks/${query ? `?${query}` : ""}`))
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }

  return (await response.json()) as TaskRecord[]
}

export async function createTask(input: TaskCreate) {
  const response = await fetch(apiUrl("/tasks/"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }

  return (await response.json()) as TaskRecord
}

export async function updateTask(taskId: string, input: TaskUpdate) {
  const response = await fetch(apiUrl(`/tasks/${taskId}`), {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }

  return (await response.json()) as TaskRecord
}

export async function copyTask(taskId: string) {
  const response = await fetch(apiUrl(`/tasks/${taskId}/copy`), {
    method: "POST",
  })
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }

  return (await response.json()) as TaskRecord
}

export async function archiveTask(taskId: string) {
  const response = await fetch(apiUrl(`/tasks/${taskId}/archive`), {
    method: "POST",
  })
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }
}

export async function unarchiveTask(taskId: string) {
  return updateTask(taskId, {
    archived_at: null,
    archived_reason: null,
  })
}
