# Proposed public `term` relation and ICU segmentation

Status: experimental, not installed in the production public registry or query
service. Classification: both a schema/access-path opportunity and a downstream
query-planning/layout gap. No production projection or new service is introduced.

## Public contract

```text
term(content_id VARCHAR, text VARCHAR, frequency BIGINT)
```

One row per distinct normalized indexed term in a content's `prose.text`.
Frequency counts occurrences in that prose, not captures. The logical key is
`(content_id, text)`; empty prose produces no rows. Dictionary IDs and postings
remain private. The declaration is [term.sql](../../../benchmarks/query/experiments/term.sql).
There is no `word` alias.

Existing HTML body prose extraction is unchanged: block boundaries and inline
concatenation follow the production extractor, with its excluded subtrees.
ICU casefold is followed by NFC normalization, then an ICU root-locale word
BreakIterator. Segments with word rule status at least 100 are retained;
punctuation and whitespace segments are discarded. Each call owns its iterator,
and slices ICU UnicodeString using ICU's UTF-16 offsets, including astral text.
No stemming, stopword removal, accent removal, synonyms or minimum length applies.

Examples: `MONKEYS` becomes `monkeys`, `Straße` becomes `strasse`, and composed
and decomposed `café` count together. `can't`, `3.14` and `foo_bar` remain intact.
ICU supplies dictionary segmentation for Japanese, Chinese and Thai; these are
search terms, not a promise of perfect linguistic words or morphological analysis.
Emoji and punctuation alone produce no terms.

SQL equality compares stored normalized text; it does not normalize query literals.
`ILIKE '%monkey%'` matches within terms, not across prose boundaries. Conjunction
proves co-occurrence in content, not phrase order; verify phrases against prose.
Heading joins return headings of matching contents, including headings that do
not themselves contain the term.

Policy: `icu-word-nfc-casefold-v1`, explicit root locale. Reports record PyICU,
ICU and Unicode versions. PyICU is now a runtime dependency pinned to 2.16.2;
the runtime image and CI pin native ICU 77.1 / Unicode 16.0, matching this run.
The shared tokenizer validates these versions and its source enters the generation
digest, so intentional ICU/data upgrades require a complete rebuild.
There is no fallback tokenizer.

## Reproduce

PyICU builds require a C++ toolchain, ICU development libraries and pkg-config.
On macOS, install ICU 77.1 and expose that installation’s pkg-config directory
when syncing (verify that the selected Homebrew formula supplies 77.1):

```sh
cd packages/periplus
PKG_CONFIG_PATH="$(brew --prefix icu4c)/lib/pkgconfig" uv sync
uv run python ../../benchmarks/query/experiments/term_surface.py \
  --input-dir ../../.periplus/agent-fixes/recovered \
  --batch-size 5 \
  --report ../../.artifacts/query-benchmarks/term-surface-icu.json
```

Debian-based development environments use the pinned build in
`docker/periplus/install-icu.sh`, plus `pkg-config` and a C++
compiler before syncing. No runtime container dependencies were changed.

Input is any directory of UTF-8 HTML files, read only and deduplicated by content
hash. The output lake is temporary and removed. Reports may contain private query
literals and remain ignored locally. Unpartitioned postings sort by term/content,
with the default row-group size. DOM joins use production node/element projectors
and public SQL views, with eight content-hash buckets; this is fixture construction,
not production batch/retention/activation integration.

Every stage compares the entire relation using EXCEPT ALL in both directions.
The reference directly tokenizes prose through an ICU Python UDF and counts in SQL;
it shares the tokenizer with materialization, so it validates counting and joins,
not independent segmentation correctness. Golden fixtures separately cover ICU
boundaries, normalization, exact frequencies, punctuation, CJK/Thai, astral offsets,
parallel iterators, empty prose, duplicates and heading semantics.

Six query families run through the shared bench in both orders at one snapshot:
rare/common equality, wildcard, conjunction, prose join and heading join. Deadlines
are 60 seconds. Reference timings include Python UDF tokenization overhead and
must not be read as a comparison against optimized native ICU or substring scans.
These are direct DuckDB timings, not query-service timings.

## ICU results, 2026-09-11

The 34 saved HTML contents contain 1,176,081 source bytes. This is a small recovery
sample, not representative production data or evidence of billion-scale performance.

| Stage | Contents | Dictionary terms | Posting rows | Dictionary bytes | Postings bytes |
| --- | --- | --- | --- | --- | --- |
| Initial | 17 | 753 | 2,208 | 10,087 | 12,343 |
| Appended | 34 | 1,310 | 4,430 | 18,144 | 24,398 |
| Compacted | 34 | 1,310 | 4,430 | 18,144 | 14,946 |

These are active Parquet sizes, excluding metadata, staging and obsolete files.
Compacted index data totals 33,090 bytes versus 33,700 bytes for prose in this sample.
All 36 paired query comparisons and complete relation equality checks passed.
Compacted public queries took approximately 3–4 ms for term lookup/conjunction,
6–7 ms for prose extraction and 16 ms for heading extraction. The direct Python
ICU reference took approximately 485–523 ms. Initially rare content matches grow
from one to two after appending, so this is not a fixed-match growth test.

## Remaining gate

The subsequent [fixed-key extraction experiment](extraction-pruning.md) confirms
both native join-filtering gaps and file-range overlap under unrelated appends.
It records a faster scoped-input variant, but the physical-read gate remains open.

The rename and tokenizer do not resolve downstream pruning. The earlier experiment
found broad node/element and prose scans despite selective content results.
Keep this experimental until fixed literal versus term-derived content IDs have
been compared through the actual public views and bounded extraction is established.
Then test a broader corpus and integrate shared dictionary ownership, retention
and activation into the production lifecycle. Do not expose private IDs or require
a special user-written join sequence to hide an optimizer gap.

Validation: `make check` passed (741 backend tests, 34 environment-dependent
skips; five SDK tests; frontend/package checks and builds). The two focused
term-surface tests and `uv lock --check` also passed.
