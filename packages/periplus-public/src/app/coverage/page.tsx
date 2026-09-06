import type { Metadata } from "next"
import { CoveragePage } from "@/components/coverage-page"
export const metadata: Metadata = { title: "Coverage", description: "See which part of the web is represented in Periplus today, with site coverage, URL counts, and observation dates." }
export default function Page() { return <CoveragePage /> }
