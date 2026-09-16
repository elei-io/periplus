"use client"

import type { CSSProperties } from "react"
import { ArrowDown, GitBranch } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

export function depthLabel(depth: number) {
  return depth === 0 ? "Just the starting page" : depth === 1 ? "One link away" : depth === 2 ? "Two links away" : `${depth} links away`
}

export function CollectionDepth({ depth, options, onChange, disabled, multipleSeeds }: {
  depth: number; options: number[]; onChange: (depth: number) => void; disabled: boolean; multipleSeeds: boolean
}) {
  const choices = [...options].sort((a, b) => a - b)
  const levels = Math.min(4, Math.max(2, ...choices))
  const rows = Array.from({ length: levels + 1 }, (_, level) => {
    const count = level === 0 ? 1 : level === 1 ? 3 : level === 2 ? 6 : 12
    return Array.from({ length: count }, (_, index) => ({ x: 30 + (index + 0.5) * 540 / count, y: 32 + level * 65 }))
  })
  return <section className="collection-depth" aria-labelledby="coverage-depth-label">
    <div className="collection-field-heading"><div><label id="coverage-depth-label">How far should we explore?</label><p>Each step follows another link from {multipleSeeds ? "each starting page" : "your starting page"}.</p></div><GitBranch aria-hidden="true" /></div>
    {choices.length <= 6 ? <div className="collection-depth-choices" role="group" aria-labelledby="coverage-depth-label">
      {choices.map(value => <Button key={value} type="button" variant={depth === value ? "default" : "outline"} disabled={disabled} aria-pressed={depth === value} onClick={() => onChange(value)}>
        <span className="collection-depth-number">{value}</span><span>{depthLabel(value)}</span>
      </Button>)}
    </div> : <Select value={depth} disabled={disabled} onValueChange={value => { if (value !== null) onChange(value) }}>
      <SelectTrigger className="w-full" aria-labelledby="coverage-depth-label"><SelectValue>{depthLabel(depth)}</SelectValue></SelectTrigger>
      <SelectContent>{choices.map(value => <SelectItem key={value} value={value}>{value} · {depthLabel(value)}</SelectItem>)}</SelectContent>
    </Select>}
    <figure className="collection-tree">
      <div className="collection-tree-origin"><span />{multipleSeeds ? "Each starting page" : "Your starting page"}</div>
      <svg viewBox={`0 0 600 ${levels * 65 + 65}`} aria-hidden="true">
        {rows.slice(1).map((nodes, index) => <g key={`links-${index}`} className="collection-tree-branch" data-active={depth > index} style={{ "--branch-delay": `${index * 70}ms` } as CSSProperties}>
          {nodes.map((node, i) => {
            const parent = rows[index][Math.floor(i * rows[index].length / nodes.length)]
            return <path key={i} d={`M ${parent.x} ${parent.y + 14} C ${parent.x} ${parent.y + 42}, ${node.x} ${node.y - 42}, ${node.x} ${node.y - 14}`} />
          })}
        </g>)}
        {rows.map((nodes, level) => <g key={level} className="collection-tree-pages" data-active={depth >= level} style={{ "--branch-delay": `${level * 70}ms` } as CSSProperties}>
          {nodes.map((node, index) => <g key={index} transform={`translate(${node.x}, ${node.y})`}>
            <rect x="-17" y="-14" width="34" height="28" rx="5" />
            <path d="M -10 -5 H 10 M -10 1 H 4 M -10 7 H 7" />
          </g>)}
        </g>)}
      </svg>
      {depth > levels && <div className="collection-tree-more"><ArrowDown aria-hidden="true" />{depth - levels} more link {depth - levels === 1 ? "step" : "steps"}</div>}
      <figcaption><span aria-live="polite">{depth === 0 ? `Collect ${multipleSeeds ? "the starting pages" : "this page"} without following links.` : `Explore up to ${depth} link ${depth === 1 ? "step" : "steps"} from ${multipleSeeds ? "each starting page" : "your starting page"}.`}</span><small>An illustration of the paths we follow. The number of pages will vary.</small></figcaption>
    </figure>
  </section>
}
