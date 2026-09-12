export const landingSql = `SELECT s.content_id, m.snippet, m.node_indexes, s.score
FROM search('artificial intelligence') s,
     unnest(s.matches) AS matches(m)
ORDER BY s.score DESC, s.content_id;`
