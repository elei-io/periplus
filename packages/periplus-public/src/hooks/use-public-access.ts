"use client";
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, extractApiError, responseJson } from "@/lib/api";
import { parseAccessPolicy, type Capability } from "@/types/access";

export function usePublicAccess(feature: Capability) {
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ["public-access"],
    queryFn: ({ signal }) =>
      fetch("/api/access", { signal, cache: "no-store" }).then(
        responseJson<unknown>,
      ),
    select: parseAccessPolicy,
    refetchInterval: 5000,
    refetchOnWindowFocus: "always",
    retry: false,
    staleTime: 0,
  });
  const cooldown = useQuery({
    queryKey: ["access-cooldown", feature],
    queryFn: async () => 0,
    enabled: false,
    initialData: 0,
  });
  const waiting = cooldown.data > query.dataUpdatedAt;
  const queueFull = feature === "crawl" && query.data?.crawl.enabled && !query.data.crawl_admission.accepting;
  const enabled = !!query.data?.[feature].enabled && !query.isError && !waiting && !queueFull;
  const message = query.isPending
    ? "Checking availability…"
    : query.isError
      ? extractApiError(query.error)
      : !query.data?.[feature].enabled
        ? `Public ${feature === "crawl" ? "crawl submissions" : feature === "sql" ? "SQL execution" : "dataset assistant"} is currently disabled.`
        : queueFull
          ? "New coverage requests are temporarily paused while the crawler catches up. Accepted requests continue; submissions reopen automatically."
        : waiting
          ? `Rate limit reached. Retry after ${new Date(cooldown.data).toLocaleTimeString()}.`
          : null;
  const onDenied = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 429 && error.code !== "crawl_queue_full")
      cache.setQueryData(
        ["access-cooldown", feature],
        Date.now() + Math.max(1, error.retryAfterSeconds ?? 5) * 1000,
      );
    void cache.invalidateQueries({ queryKey: ["public-access"] });
  }, [cache, feature]);
  return { data: query.isError ? undefined : query.data, enabled, retryEnabled: !!query.data && !query.isError && !waiting, message, onDenied };
}
