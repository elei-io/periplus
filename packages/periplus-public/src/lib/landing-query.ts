export const landingSql = `SELECT title, url, snippet, score
FROM search('artificial intelligence')
ORDER BY score DESC, content_id;`
