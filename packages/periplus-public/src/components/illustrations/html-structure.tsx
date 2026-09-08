"use client"

import { useState, useSyncExternalStore } from "react"
import { Code2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Table, TableHeader, TableHead, TableBody, TableRow, TableCell } from "@/components/ui/table"
import styles from "./illustrations.module.css"

const source = `<!doctype html>
<html lang="en">
<head><title>Field notes</title></head>
<body>
  <article data-region="coast">
    <h1>Life along the coastline</h1>
    <p>A record of <em>local observations</em>.</p>
    <a href="/sources">Read the sources</a>
  </article>
</body>
</html>`
const sourceLines = source.split("\n")
const linesByTag: Record<string, number> = { html: 1, head: 2, title: 2, body: 3, article: 4, h1: 5, p: 6, em: 6, a: 7 }
type ElementRow = { index: number; parent: number | null; tag: string; attributes: Record<string, string>; direct: string | null; tail: string | null }

// Deliberately scoped to this fixed HTML sample, not an alternative catalogue parser.
function parseSample(): ElementRow[] {
  const document = new DOMParser().parseFromString(source, "text/html")
  const elements = Array.from(document.querySelectorAll("*"))
  const indices = new Map(elements.map((element, index) => [element, index]))
  function textUntilElement(first: ChildNode | null): string | null {
    let text = ""
    for (let node = first; node && node.nodeType !== Node.ELEMENT_NODE; node = node.nextSibling) {
      if (node.nodeType === Node.TEXT_NODE) text += node.textContent
    }
    return text || null
  }
  return elements.map((element, index) => ({ index, parent: indices.get(element.parentElement!) ?? null, tag: element.localName,
    attributes: Object.fromEntries(Array.from(element.attributes, attribute => [attribute.name, attribute.value])),
    direct: textUntilElement(element.firstChild), tail: textUntilElement(element.nextSibling),
  }))
}
// Stable snapshots let server rendering hydrate before using the browser DOM parser.
const emptyRows: ElementRow[] = []
let browserRows: ElementRow[] | undefined
const subscribe = () => () => {}
const getBrowserRows = () => browserRows ??= parseSample()
const getServerRows = () => emptyRows

function literal(value: string | null) { return value === null ? "NULL" : JSON.stringify(value) }
function SourceLine({ text }: { text: string }) {
  return <>{text.split(/("[^"]*"|<\/?[\w-]+|[\w-]+=)/g).map((part, index) => <span key={index} className={part.startsWith('"') ? styles.string : part.startsWith("<") ? styles.tag : part.endsWith("=") ? styles.attribute : undefined}>{part}</span>)}</>
}

export function HtmlStructureIllustration() {
  const rows = useSyncExternalStore(subscribe, getBrowserRows, getServerRows)
  const [selectedTag, setSelectedTag] = useState("h1")
  const selected = rows.find(row => row.tag === selectedTag)
  function select(tag: string) { setSelectedTag(tag) }
  return <figure className={styles.figure} aria-label="Interactive HTML to rows illustration">
    <div className={styles.caption}><span><Code2 aria-hidden="true" />HTML → structured rows</span></div>
    <div className={styles.frame}>
      <div className={styles.split}>
        <div className={styles.sourcePanel}><div className={styles.bar}>coast.html <span>Illustrative source</span></div><div className={styles.source} aria-label="HTML source">{sourceLines.map((line, index) => {
          const tag = Object.keys(linesByTag).find(tag => linesByTag[tag] === index)
          return <button key={index} type="button" className={`${styles.sourceLine} ${selected && linesByTag[selected.tag] === index ? styles.selected : ""}`} disabled={!tag} aria-pressed={Boolean(selected && linesByTag[selected.tag] === index)} onClick={() => tag && select(tag)}><span className={styles.lineNumber}>{index + 1}</span><code><SourceLine text={line} /></code></button>
        })}</div></div>
        <div className={styles.rowsPanel}><div className={styles.bar}>content.html_element <span>{rows.length ? `${rows.length} rows` : "Preparing rows"}</span></div>{rows.length ? <Table aria-label="Illustrative HTML element rows"><TableHeader><TableRow><TableHead>index</TableHead><TableHead>parent</TableHead><TableHead>tag</TableHead><TableHead>text_direct</TableHead></TableRow></TableHeader><TableBody className={styles.reveal}>{rows.map(row => <TableRow key={row.index} className={selected?.index === row.index ? styles.selected : undefined}><TableCell>{row.index}</TableCell><TableCell>{row.parent ?? "NULL"}</TableCell><TableCell><Button variant="ghost" size="sm" aria-label={`Inspect ${row.tag} row`} aria-pressed={selected?.index === row.index} onClick={() => select(row.tag)} className={styles.rowButton}>{row.tag}</Button></TableCell><TableCell>{literal(row.direct)}</TableCell></TableRow>)}</TableBody></Table> : <div className={styles.empty}><Code2 aria-hidden="true" /><strong>From tags to rows.</strong><span>Preparing the sample structure…</span></div>}</div>
      </div>
      {selected && <dl className={styles.fields} aria-live="polite"><div><dt>attributes</dt><dd>{JSON.stringify(selected.attributes)}</dd></div><div><dt>text_direct</dt><dd>{literal(selected.direct)}</dd></div><div><dt>text_tail</dt><dd>{literal(selected.tail)}</dd></div></dl>}
    </div>
    <figcaption className={styles.note}>Interactive sample, parsed locally by your browser. Selected fields illustrate the public model; this is not a live corpus query or the Periplus production parser. Whitespace uses JSON escapes.</figcaption>
  </figure>
}
