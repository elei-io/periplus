"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import styles from "./illustrations.module.css"
const parts = [
  { text: "A record of ", sql: 'p.text_direct = "A record of "', explanation: "Text before the first child belongs to the paragraph." },
  { text: "local observations", sql: 'em.text_direct = "local observations"', explanation: "The nested element owns the emphasized words." },
  { text: ".", sql: 'em.text_tail = "."', explanation: "The full stop follows the closing em tag. It remains available as trailing text." },
]
export function TextPlacementIllustration() {
  const [selected, setSelected] = useState(1)
  return <figure className={styles.figure} aria-label="Interactive HTML text placement illustration"><div className={styles.caption}><span>The words between the tags</span><span className={styles.note}>Select a text segment</span></div><div className={styles.frame}><pre className={styles.query}><code><span className={styles.tag}>{"<p>"}</span>A record of <span className={styles.tag}>{"<em>"}</span>local observations<span className={styles.tag}>{"</em>"}</span>.<span className={styles.tag}>{"</p>"}</span></code></pre><div className={styles.segments}>{parts.map((part, index) => <Button key={part.sql} variant={selected === index ? "secondary" : "ghost"} aria-pressed={selected === index} onClick={() => setSelected(index)}>{index === 1 ? <em>{part.text}</em> : part.text}</Button>)}</div><div className={styles.textDetail} aria-live="polite"><code>{parts[selected].sql}</code><p>{parts[selected].explanation}</p></div></div><figcaption className={styles.note}>Illustrative HTML. Keeping hierarchy and text placement gives your extraction rules access to the document’s structure.</figcaption></figure>
}
