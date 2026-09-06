import Link from "next/link"
export function PublicFooter() {
  return <footer className="public-footer"><span>Periplus · Free public preview</span><nav aria-label="Footer navigation"><Link href="/about#access">Access & data use</Link><Link href="/coverage">Coverage</Link><Link href="/suggest">Suggest coverage</Link><a href="mailto:ekku.leivonen@elei.io?subject=Periplus%20feedback">Feedback & contact</a></nav></footer>
}
