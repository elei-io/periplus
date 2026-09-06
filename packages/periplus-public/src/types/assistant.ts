import type { InferAgentUIMessage } from "ai"
import type { createDiscoveryAgent } from "@/server/discovery-agent"
export type DiscoveryMessage = InferAgentUIMessage<ReturnType<typeof createDiscoveryAgent>>
