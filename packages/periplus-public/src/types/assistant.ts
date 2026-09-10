import type { InferAgentUIMessage } from "ai"
import type { createDiscoveryAgent } from "@/server/discovery-agent"
import type { DiscoveryAnalyticsMetadata } from "./analytics"
export type DiscoveryMessage = InferAgentUIMessage<ReturnType<typeof createDiscoveryAgent>, DiscoveryAnalyticsMetadata>
