import { FrontierItemDetail } from "@/components/frontier-items"
export default async function Page({ params }: PageProps<"/frontier/[id]">) { const { id } = await params; return <main className="discovery-workspace flex flex-col gap-5"><FrontierItemDetail id={id} /></main> }
