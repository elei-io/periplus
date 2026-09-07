import { FrontierItemDetail } from "@/components/frontier-items"
export default async function Page({ params }: PageProps<"/frontier/[id]">) { const { id } = await params; return <main className="flex flex-col gap-5 p-6"><FrontierItemDetail id={id} /></main> }
