import { coverageSql } from "@/lib/datasets"
import { sqlDraftLink } from "@/lib/schema-reference"
import Link from "next/link"
export function PublicFooter() {
  return <footer className="public-footer"><span>Periplus · Web research, built together</span><nav aria-label="Footer navigation"><Link href="/about#access">Access & data use</Link><Link href={sqlDraftLink(coverageSql)}>Coverage</Link><Link href="/coverage">Request coverage</Link><a href="mailto:ekku.leivonen@elei.io?subject=Periplus%20feedback">Feedback & contact</a></nav></footer>
}
