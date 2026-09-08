# Discover books mentioning robots

Both queries search captured book product pages on books.toscrape.com for the
case-insensitive substring `robot`, then return distinct URL/title pairs. Category
pages are excluded. This is a historical-content search, not a latest-capture
selection or a claim that every matching book is about robotics. Navigation and
footer mentions qualify, consistent with the public prose contract.

- [Without prose](without-prose.sql) reconstructs body text from ordered DOM
  nodes. Running subtree-end maxima exclude script, style, template and noscript
  descendants without a corpus-wide ancestor join. Block-entry and block-exit
  events prevent accidental word concatenation across block boundaries.
- [With prose](with-prose.sql) reads the precomputed body text and performs the
  same content-ID joins and extraction.

The single-token predicate is unaffected by whitespace collapsing. Do not simply
replace it with a multiword phrase in the baseline: that would also require the
same whitespace normalization as prose. Both queries use immediate h1 text for
book titles, which is complete on this source; sites with nested title markup
need descendant-text extraction instead.

The queries use only public SQL relations and are also available on the public
site's Documentation page. No special query compiler rewrite or external search
index is involved. Measurements on this development corpus do not establish
production-scale performance or prove that selective joins avoid all DOM scans.


## Verified on the local deployment, 2026-09-09

Rebuild `07ec6868-e9fa-4a20-b869-5a543ffb331e` completed all 43 batches and
2,124 visits, activating at snapshot 8438. The active prose relation contains
1,478 unique content rows and 9,820,624 text characters.

Both saved queries executed successfully through the public HTTP endpoint at
snapshot 8439. They returned identical ordered results: 11 URL/title pairs,
matching VARCHAR column types, and no truncation. Examples include The Wild Robot,
Danganronpa Volume 1, and Saga, Volume 5.

| Query | Reported service elapsed time |
| --- | ---: |
| Without prose | 1441.8 ms |
| With prose | 152.6 ms |

These are single sequential runs, baseline first, on a small local corpus after
activation. Cache state and host load were not controlled; this is functional
validation with illustrative timings, not a production speedup estimate.
