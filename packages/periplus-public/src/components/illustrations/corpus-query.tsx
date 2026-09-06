"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Table, TableHeader, TableHead, TableBody, TableRow, TableCell } from "@/components/ui/table"
import styles from "./illustrations.module.css"

const pages = [
  ["fieldnotes", "Coastal communities"], ["fieldnotes", "Forest observations"], ["fieldnotes", "Water quality"], ["fieldnotes", "Coast and water"],
  ["atlas", "Coast maps"], ["atlas", "Forest canopy"], ["atlas", "Water routes"], ["atlas", "Forest trails"],
  ["journal", "Coast restoration"], ["journal", "Coastal plants"], ["journal", "Forest recovery"], ["journal", "Freshwater habitats"],
]
export function CorpusQueryIllustration() {
  const [term, setTerm] = useState("coast")
  const matching = pages.filter(([, heading]) => heading.toLowerCase().includes(term))
  const groups = Object.entries(matching.reduce<Record<string, number>>((counts, [site]) => { counts[site] = (counts[site] ?? 0) + 1; return counts }, {})).sort(([a, countA], [b, countB]) => countB - countA || a.localeCompare(b))
  return <figure className={styles.figure} aria-label="Interactive cross-page analysis illustration">
    <div className={styles.caption}><span>One query. Multiple documents.</span><div className={styles.actions}>{["coast", "forest", "water"].map(value => <Button size="sm" variant={term === value ? "secondary" : "ghost"} key={value} aria-pressed={term === value} onClick={() => setTerm(value)}>{value}</Button>)}</div></div>
    <div className={styles.frame}><pre className={styles.query}><code><span className={styles.tag}>SELECT</span>{" site, count(*) AS matching_pages\n"}<span className={styles.tag}>FROM</span>{" demo.pages\n"}<span className={styles.tag}>WHERE</span>{" lower(heading) LIKE "}<span className={styles.string}>{`'%${term}%'`}</span>{"\n"}<span className={styles.tag}>GROUP BY</span>{" site\n"}<span className={styles.tag}>ORDER BY</span>{" matching_pages DESC, site;"}</code></pre><div className={styles.querySplit}><div><div className={styles.bar}>Sample corpus <span>12 pages / 3 fictional sites</span></div><div className={styles.pages}>{pages.map(([site, heading]) => <div key={heading} className={`${styles.page} ${heading.toLowerCase().includes(term) ? styles.match : styles.dim}`}><span>{heading}</span><small>{site}.example</small></div>)}</div></div><div><div className={styles.bar}>Result <span>Grouped by site</span></div>{<Table aria-label="Illustrative query result"><TableHeader><TableRow><TableHead>site</TableHead><TableHead>matching_pages</TableHead></TableRow></TableHeader><TableBody key={term} className={styles.reveal}>{groups.map(([site, count]) => <TableRow key={site}><TableCell>{site}.example</TableCell><TableCell>{count}</TableCell></TableRow>)}</TableBody></Table>}</div></div><div className={styles.status} aria-live="polite">{`12 sample pages → ${matching.length} matches → ${groups.length} grouped rows`}</div></div>
    <figcaption className={styles.note}>Illustrative data and a simplified demo.pages relation. Filtering and aggregation run locally in JavaScript; no live query or performance benchmark.</figcaption>
  </figure>
}
