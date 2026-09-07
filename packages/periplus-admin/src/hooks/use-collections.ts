import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  Collection,
  CollectionChange,
  CreateCollection,
  CollectionHistoryPage,
  CollectionPage,
} from "@/types/collections"

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(path), {
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<T>
}

export function useCollections(offset: number, status: string) {
  return useQuery({
    queryKey: ["collections", { offset, status }],
    queryFn: ({ signal }) =>
      read<CollectionPage>(
        `/collections?limit=20&offset=${offset}${status ? `&status=${encodeURIComponent(status)}` : ""}`,
        signal
      ),
    refetchInterval: 5000,
    retry: false,
  })
}
export function useCollection(id: string) {
  return useQuery({
    queryKey: ["collection", id],
    queryFn: ({ signal }) =>
      read<Collection>(`/collections/${encodeURIComponent(id)}`, signal),
    refetchInterval: 5000,
    retry: false,
  })
}
export function useCollectionHistory(cursor: string | null) {
  return useQuery({
    queryKey: ["collection-history", cursor],
    queryFn: ({ signal }) =>
      read<CollectionHistoryPage>(
        `/collections/history?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
        signal
      ),
    retry: false,
  })
}
export function useChangeCollection(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (change: CollectionChange) => {
      const priority = "priority" in change
      const response = await fetch(
        apiUrl(
          `/collections/${encodeURIComponent(id)}/${priority ? "priority" : "actions"}`
        ),
        {
          method: priority ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          signal: AbortSignal.timeout(15000),
          body: JSON.stringify(change),
        }
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
      return response.json() as Promise<Collection>
    },
    onSuccess: (value) => {
      client.setQueryData(["collection", id], value)
      void client.invalidateQueries({ queryKey: ["collections"] })
      toast.success("Collection updated.")
    },
    onError: (error) => {
      toast.error(extractApiError(error))
      void client.invalidateQueries({ queryKey: ["collection", id] })
    },
  })
}

export function useSubmitCollection() {
  const client = useQueryClient()
  return useMutation({
    retry: false,
    mutationFn: async (payload: CreateCollection) => {
      const response = await fetch(apiUrl("/collections"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(30000),
        body: JSON.stringify(payload),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return response.json() as Promise<Collection>
    },
    onSuccess: (value) => {
      client.setQueryData(["collection", value.id], value)
      void client.invalidateQueries({ queryKey: ["collections"] })
      toast.success("Collection submitted.")
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
