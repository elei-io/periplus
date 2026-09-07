import Link from "next/link"
export function PublicFooter() {
  return <footer className="public-footer"><span>Periplus · A public web observatory</span><nav aria-label="Footer navigation"><Link href="/about#access">Access & data use</Link><Link href="/observatory#coverage">Coverage</Link><Link href="/observatory">Suggest a starting point</Link><a href="mailto:ekku.leivonen@elei.io?subject=Periplus%20feedback">Feedback & contact</a></nav></footer>
}
