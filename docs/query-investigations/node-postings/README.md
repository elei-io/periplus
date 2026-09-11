# Node postings: semantic gate

Classification: schema/catalogue design. No compiler intervention or production change.
Hypothesis: independently tokenizing eligible body text nodes yields postings whose
frequencies sum to existing content postings and supports matching-node parent lookup.

Run from packages/periplus:

```
uv run python ../../benchmarks/query/experiments/node_posting.py
```

The six deterministic fixtures use the installed pinned ICU tokenizer and production
HTML parser/prose extraction. Pricing matches locate span/strong parents; repeated
terms sum correctly; head/script text is excluded; Japanese, Chinese, accents and
astral punctuation are exercised. These are synthetic semantic fixtures, not corpus
performance measurements.

Result: reject independent node tokenization as an implementation of existing term
semantics. `mon<strong>key</strong>` yields content term `monkey` but node terms
`mon` and `key`. A combining accent across inline boundaries similarly changes
`café` into unrelated node tokens. Four fixtures agree, two disagree.

Next candidate: tokenize the same full prose stream while retaining a source-node
map through whitespace normalization, case folding, NFC and UTF-16 boundaries.
Associate each occurrence with all contributing text nodes. This preserves content
search matches, but a cross-node occurrence touches multiple nodes: node frequencies
then count intersecting occurrences and MUST NOT be summed as content frequency.
Alternatively store an occurrence identity/position plus contributing node indexes,
which permits exact occurrence deduplication and later phrase work at extra cost.
Do not silently anchor solely to the first node: that loses contributing elements
for structural analysis.

Before a physical-layout decision, prove source mapping on inline splits, combining
characters, case-fold expansion, block separators, excluded subtrees and multilingual
word boundaries. Then use the shared query bench for equal-result comparisons of
content-clustered flat node postings versus nested postings, including append overlap,
fixed candidate sets, parent/ancestor retrieval, bytes/files read and storage size.
No layout speedup or production readiness is established by this probe.
