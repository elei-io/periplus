import { LivePage } from "@/components/live-page"
import type { Metadata } from "next"
export const metadata: Metadata = { title: "Crawler Live", description: "Current public crawler activity, recent captures, and recorded acquisition velocity." }
export default function Page() { return <LivePage /> }
