import type { Metadata } from "next"
import { hasWorkspaceInput, pageMetadata } from "@/lib/seo"
import { datasetDraftSchema } from "@/types/answer"
import { DiscoveryWorkspace } from "@/components/discovery-workspace"

export async function generateMetadata({ searchParams }: PageProps<"/build">): Promise<Metadata> {
  const params = await searchParams
  return pageMetadata("/build", "Build a dataset", "Define exact columns, types and nullability. Build and validate a dataset from collected web data.", !hasWorkspaceInput(params, ["question", "draft"]))
}

export default async function BuildPage({ searchParams }: PageProps<"/build">) {
  const params = await searchParams
  const question = typeof params.question === "string" ? params.question : undefined
  let draft
  if (typeof params.draft === "string" && params.draft.length <= 64000) {
    try { const parsed = datasetDraftSchema.safeParse(JSON.parse(params.draft)); if (parsed.success) draft = parsed.data } catch { /* Invalid links open an empty specification. */ }
  }
  return <DiscoveryWorkspace key={JSON.stringify(params)} mode="build" autoRun={false} question={question} draft={draft} />
}
