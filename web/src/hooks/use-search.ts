import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { readSseStream } from "@/lib/sse"
import type {
  Chat,
  ChatGraphRunSubmission,
  ChatItem,
  ChatList,
  SearchEvent,
} from "@/types/search"

const chatsKey = ["chats"] as const

async function jsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) throw await apiErrorFromResponse(response)
  return (await response.json()) as T
}

export function useChats() {
  return useQuery({
    queryKey: chatsKey,
    queryFn: async () =>
      jsonResponse<ChatList>(await fetch(apiUrl("/chats/"))),
  })
}

export function useChat(chatId: string | null) {
  return useQuery({
    queryKey: [...chatsKey, chatId],
    enabled: chatId !== null,
    queryFn: async () =>
      jsonResponse<Chat>(await fetch(apiUrl(`/chats/${chatId}`))),
  })
}

export function useCreateChat() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      jsonResponse<Chat>(
        await fetch(apiUrl("/chats/"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        })
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: chatsKey })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useDeleteChat() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (chatId: string) => {
      const response = await fetch(apiUrl(`/chats/${chatId}`), {
        method: "DELETE",
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: chatsKey })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export async function streamChatTurn(
  chatId: string,
  message: string,
  onEvent: (event: SearchEvent) => void,
  signal?: AbortSignal
) {
  const response = await fetch(apiUrl(`/chats/${chatId}/turns/stream`), {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message }),
    signal,
  })
  if (!response.ok) throw await apiErrorFromResponse(response)

  await readSseStream(response, (messageEvent) => {
    const event = JSON.parse(messageEvent.data) as SearchEvent
    if (event.type !== messageEvent.event) {
      throw new Error("The Atlas agent returned an invalid event stream.")
    }
    onEvent(event)
  })
}

export async function streamSearch(
  question: string,
  onEvent: (event: SearchEvent) => void,
  signal?: AbortSignal
) {
  const chat = await jsonResponse<Chat>(
    await fetch(apiUrl("/chats/"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
      signal,
    })
  )
  await streamChatTurn(chat.id, question, onEvent, signal)
}

export function useRunChatPlan(chatId: string, itemId: string, planIndex = 0) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (maxCrawls: number) =>
      jsonResponse<ChatGraphRunSubmission>(
        await fetch(apiUrl(`/chats/${chatId}/items/${itemId}/run`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ max_crawls: maxCrawls, plan_index: planIndex }),
        })
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [...chatsKey, chatId] })
      await queryClient.invalidateQueries({ queryKey: chatsKey })
      await queryClient.invalidateQueries({ queryKey: ["graph-runs"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useRecordChatSchedule(chatId: string, itemId: string, planIndex = 0) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (scheduleId: string) =>
      jsonResponse<ChatItem>(
        await fetch(apiUrl(`/chats/${chatId}/items/${itemId}/schedule`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ schedule_id: scheduleId, plan_index: planIndex }),
        })
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [...chatsKey, chatId] })
      await queryClient.invalidateQueries({ queryKey: chatsKey })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useApplyChatScheduleChange(chatId: string, itemId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (proposalIndex: number) =>
      jsonResponse<ChatItem>(
        await fetch(apiUrl(`/chats/${chatId}/items/${itemId}/schedule-change`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ proposal_index: proposalIndex }),
        })
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [...chatsKey, chatId] })
      await queryClient.invalidateQueries({ queryKey: chatsKey })
      await queryClient.invalidateQueries({ queryKey: ["crawl-schedules"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
