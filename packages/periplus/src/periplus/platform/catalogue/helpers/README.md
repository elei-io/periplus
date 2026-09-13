# Public helpers

`html_search(terms)` searches immutable page-word postings. Supply at most 32
case-folded, NFC-normalized word keys as a VARCHAR list. It returns `content_id`,
`node_indexes` and `score`; score counts distinct requested words found per content.
Matching is any-word and node indexes combine their containing elements. Inputs
are exact index keys, not a free-text query parser or substring search.

```sql
SELECT * FROM html_search(['robot', 'science'])
ORDER BY score DESC, content_id LIMIT 20;
```

`HELPERS` is the explicit registry consumed by catalogue installation and
`GET /query/helpers`. The SQL resource lives under `sql/public_v1/helpers/`.
The fixed 32 term slots plus a NULL sentinel preserve a required literal filter
after macro binding;
a relation-producing UNNEST join can lose that access path. Keep the input limit
and slots consistent, and validate real DuckLake plans when changing this SQL.
