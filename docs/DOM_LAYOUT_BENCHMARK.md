# DOM physical layout benchmark

## Decision

Keep `material.html_elements` at one row per immutable content element.

Do not replace it with one nested DOM row per content identity. Do not adopt the tested
2,048-element block layout as the canonical relation. Both nested layouts reduce compressed
storage slightly and accelerate bounded CSS extraction, but their peak memory and broad-query
behavior conflict with Atlas's capacity goal.

The next DOM optimization should preserve the flat canonical relation and make exact, bounded
content-ID hydration a query-execution capability. It should not duplicate the structural DOM.

## Goal and corpus

The benchmark prioritizes predictable capacity over minimum latency: users should be able to run
ordinary content analysis without accidental multi-gigabyte blocking state or execution that grows
with unrelated lake contents.

The development snapshot contained:

- 6,861 visits;
- 6,801 distinct projected HTML contents; and
- 7,299,300 structural elements.

Every layout was built from the same snapshot into disposable local Parquet. Measurements used
DuckDB 1.5.5, four threads, a fresh connection per query/layout, one normal execution, and two warm
`EXPLAIN ANALYZE` executions. The current Atlas extension supplied both native selector paths.

The executable benchmark and complete SQL cases live in
[`backend/scripts/benchmark_dom_layouts.py`](../backend/scripts/benchmark_dom_layouts.py).

## Compared layouts

### Flat element rows

The current shape:

```text
content_id, element_index, parent_index, subtree_end_index
depth, child_index, tag, namespace, attributes, text_direct, text_tail
```

CSS selection uses the native table-in/table-out operator, which reconstructs and retains at most
one selected document at a time.

### One packed row per document

```text
content_id, nodes LIST<STRUCT<element fields...>>
```

CSS selection uses the extension's existing native scalar selector over `LIST<STRUCT>`. Relational
`dom.element` semantics are recovered by unnesting `nodes`.

### Bounded parallel-array blocks

One row contains at most 2,048 elements:

```text
content_id, block_index
element_indexes[], parent_indexes[], subtree_end_indexes[]
depths[], child_indexes[], tags[], namespaces[]
attribute_maps[], direct_texts[], tail_texts[]
```

A position-driven public-shaped view anchors cardinality on `element_indexes` and indexes only the
arrays required by the user's projection. This preserves more column pruning than `LIST<STRUCT>`
and bounds ordinary expansion to one block. CSS selection combines the selected document's blocks
and uses the native packed-list selector.

## End-to-end query set

| Query | User scenario | Scope |
|---|---|---:|
| `global_tag_frequency` | Discover the corpus's dominant structural vocabulary | All elements |
| `cross_site_structure_trends` | Compare semantic/documentation structures across six major sites | Current pages |
| `bounded_dom_distribution` | Measure DOM size, depth, and attribute distributions | 1,000 contents |
| `record_shape_discovery` | Statistically identify repeated record-like element shapes | 500 contents |
| `image_accessibility_by_site` | Audit missing and empty image alternative text | 1,500 contents |
| `documentation_term_trends` | Compare deprecation, experimental, and compatibility language | 1,500 contents |
| `extract_main_links` | Extract main-content links from MDN pages | 100 contents |
| `extract_form_fields` | Extract named form controls | 500 contents |
| `extract_headings` | Extract article and main-content headings | 500 contents |

All nine result bags were exactly equal across all three layouts.

## Results

Warm latency is the median of two executions. Peak memory is DuckDB's connection-local
`system_peak_buffer_memory`; every measurement used a fresh connection.

| Query | Flat ms / MiB | Document ms / MiB | Block ms / MiB |
|---|---:|---:|---:|
| Global tag frequency | 53 / 279 | 657 / 2,355 | 393 / 1,365 |
| Cross-site structure trends | 31 / 113 | 6,495 / 12,188 | 353 / 1,310 |
| Bounded DOM distribution | 162 / 157 | 591 / 2,339 | 1,692 / 3,419 |
| Record-shape discovery | 206 / 541 | 9,288 / 13,068 | 2,055 / 3,995 |
| Image accessibility by site | 157 / 150 | 6,501 / 12,152 | 1,637 / 3,793 |
| Documentation term trends | 176 / 177 | 622 / 2,458 | 684 / 2,133 |
| Extract main links | 1,152 / 318 | 851 / 2,281 | 930 / 2,070 |
| Extract form fields | 4,172 / 517 | 1,738 / 2,361 | 1,825 / 2,273 |
| Extract headings | 4,203 / 693 | 1,713 / 2,374 | 1,831 / 2,323 |

Compressed storage was close:

| Layout | Bytes | MiB |
|---|---:|---:|
| Flat | 170,762,912 | 162.9 |
| Document | 152,554,716 | 145.5 |
| Block | 152,610,370 | 145.5 |

The nested layouts saved about 11% because they did not repeat content identity on every element.
That saving is not large enough to justify their execution-state cost.

## Findings

### Whole-document packing is not capacity-safe

Broad element queries still expand the same logical element population, but nested payloads pass
through a lateral expansion boundary before ordinary joins and filters. The worst tested plans
retained 12–13 GB at only 7.3 million elements. This cannot be the canonical shape for a lake
intended to hold tens of billions of elements.

Building all packed documents through one global `list()` aggregation also exhausted DuckDB's
aggregate state. The disposable builder had to process deterministic content-hash prefixes. Atlas's
visit-batched materializer is already bounded, but any packed rebuild design would have to preserve
that boundary explicitly.

### The block hybrid improves plans, but not enough

The first parallel-`UNNEST` view caused DuckDB to read every array even for tag-only queries. A
position-driven expansion reduced the tag query from approximately 678 ms and 2.20 GB to 393 ms
and 1.37 GB by reading only `element_indexes` and `tags`.

Queries requiring several element fields still retained 2–4 GB. Compared with flat rows, the final
block design was 3.9–11.3 times slower for broad analysis and used 4.9–25.3 times as much memory.

### Packed selectors are faster but memory-heavier

The extension's existing scalar selector over `LIST<STRUCT>` made the extraction jobs 1.3–2.4
times faster than the flat table-in/table-out path. The packed and block paths nevertheless used
3.3–7.2 times the peak memory.

Feeding expanded block rows through the streaming table-in/table-out selector was also tested for
the 100-page link extraction. It took 4.33 seconds and retained 6.98 GB, so that extension path was
rejected. No native code change was retained from the experiment.

### Flat rows best preserve arbitrary SQL capacity

Flat rows let DuckDB project narrow element columns, filter vectors before blocking consumers, and
stream ordinary relational scans. Every broad or statistical query was fastest on flat rows, and
no test exceeded 693 MiB peak memory.

The flat extraction plans still scan unrelated element rows before reducing to the selected
content identities. That is a compiler/physical-access problem, not evidence that the canonical
storage grain is wrong.

## Recommended course

1. Retain the current public `dom.*` contract and flat `material.html_elements` relation.
2. Keep this query set as the regression suite for future DOM compiler and physical-layout work.
3. Restore exact, bounded document-scope hydration as an ephemeral execution strategy, but require
   one pinned snapshot and differential result equality.
4. Make exact content-ID lookup into the flat relation proportional to selected content rather
   than total lake elements. Investigate the smallest DuckLake/DuckDB row-group or keyed-access
   primitive before introducing an Atlas-owned index.
5. Continue enforcing the one-document selector element ceiling and the interactive content-scope
   warning. A rewrite that cannot prove its scope must fail closed or retain the authored plan; it
   must never return a partial DOM.

This keeps one structural truth, protects arbitrary element-oriented SQL, and directs native work
at the measured weakness: locating selected flat rows without scanning unrelated contents.
