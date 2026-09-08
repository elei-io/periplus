"use client"

import Link from "next/link"
import { useEffect, useRef, useState } from "react"
import { ArrowUpRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, TableCaption } from "@/components/ui/table"
import { featuredDataset } from "@/lib/datasets"
import { sqlDraftLink } from "@/lib/schema-reference"

export function LandingExample() {
  const root = useRef<HTMLElement>(null)
  const [playing, setPlaying] = useState(false)
  const [field, setField] = useState("title")
  useEffect(() => {
    if (!root.current) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setPlaying(true); observer.disconnect() }
    }, { threshold: 0.3 })
    observer.observe(root.current)
    return () => observer.disconnect()
  }, [])

  return <section ref={root} id="example" className="landing-example" aria-labelledby="example-heading">
    <div className="featured-dataset-heading">
      <div><span className="eyebrow">Data already here. A query of your own.</span><h2 id="example-heading">Imagine the web as a spreadsheet.</h2></div>
    </div>
    <div className="extraction-demo" data-playing={playing} data-field={field}>
      <Card className="extraction-source">
        <CardHeader><CardDescription>01 / Shared web data</CardDescription><CardTitle><h3>A page in Periplus</h3></CardTitle></CardHeader>
        <CardContent><div className="source-code" aria-label="Example HTML">
          <code>{'<div class="product_main">'}</code>
          <code className="field-title">{'  <h1>A Field Guide</h1>'}</code>
          <code className="field-price">{'  <p class="price_color">£18.00</p>'}</code>
          <code>{'</div>'}</code>
        </div></CardContent>
      </Card>
      <Card className="extraction-query">
        <CardHeader><CardDescription>02 / Your SQL</CardDescription><CardTitle><h3>Choose the fields</h3></CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="extraction-fields"><span>Extract</span><Button variant="ghost" size="sm" className="field-title" aria-pressed={field === "title"} onClick={() => setField("title")} onFocus={() => setField("title")} onMouseEnter={() => setField("title")}>title</Button><Button variant="ghost" size="sm" className="field-price" aria-pressed={field === "price"} onClick={() => setField("price")} onFocus={() => setField("price")} onMouseEnter={() => setField("price")}>price</Button></div>
          <p>Match the heading and its neighboring price. Keep the page URL as the source.</p>
          <Link className="story-link" href={sqlDraftLink(featuredDataset.sql)}>Inspect the extraction SQL <ArrowUpRight aria-hidden="true" /></Link>
        </CardContent>
      </Card>
      <Card className="extraction-result">
        <CardHeader><CardDescription>03 / Your dataset</CardDescription><CardTitle><h3>A row you can use</h3></CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableCaption>Illustrative example · not live results</TableCaption>
            <TableHeader><TableRow><TableHead scope="col">title</TableHead><TableHead scope="col">price_gbp</TableHead></TableRow></TableHeader>
            <TableBody><TableRow><TableCell className="field-title">A Field Guide</TableCell><TableCell className="field-price">18.00</TableCell></TableRow></TableBody>
          </Table>
          <p className="story-note">Source URLs and collection dates travel with your results.</p>
        </CardContent>
      </Card>
    </div>
    <Link className="story-link example-guide" href="/docs#recipe">Follow the worked example <ArrowUpRight aria-hidden="true" /></Link>
  </section>
}
