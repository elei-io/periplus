"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, responseJson } from "@/lib/api";
import type { AccessPolicy, Capability } from "@/types/access";

export function usePublicAccess(feature: Capability) {
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ["public-access"],
    queryFn: ({ signal }) =>
      fetch("/api/access", { signal, cache: "no-store" }).then(
        responseJson<AccessPolicy>,
      ),
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
  const enabled = !!query.data?.[feature].enabled && !query.isError && !waiting;
  const message = query.isPending
    ? "Checking availability…"
    : query.isError
      ? "Availability could not be checked. Please try again shortly."
      : !query.data?.[feature].enabled
        ? `Public ${feature === "crawl" ? "crawl submissions" : feature === "sql" ? "SQL execution" : "dataset assistant"} is currently disabled.`
        : waiting
          ? `Rate limit reached. Retry after ${new Date(cooldown.data).toLocaleTimeString()}.`
          : null;
  function onDenied(error: unknown) {
    if (error instanceof ApiError && error.status === 429)
      cache.setQueryData(
        ["access-cooldown", feature],
        Date.now() + Math.max(1, error.retryAfterSeconds ?? 5) * 1000,
      );
    void cache.invalidateQueries({ queryKey: ["public-access"] });
  }
  return { ...query, enabled, message, onDenied };
}
