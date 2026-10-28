SHOW CREATE TABLE judian.chunks\G
SELECT c.id, LEFT(d.title, 12) AS doc, LEFT(c.heading, 18) AS heading,
       ROUND(1 - cosine_distance(c.embedding, q.v), 4) AS cos,
       ROUND(MATCH(c.text) AGAINST('seekdb 怎么查看系统日志？' IN NATURAL LANGUAGE MODE), 2) AS ft,
       v.rk AS vec_rank, f.rk AS ft_rank,
       ROUND(1/(60+v.rk) + COALESCE(1/(60+f.rk), 0), 6) AS rrf
FROM judian.chunks c
JOIN judian.documents d ON d.id = c.doc_id
JOIN judian_scratch.qvec q ON q.id = 3
JOIN (SELECT c2.id AS id, ROW_NUMBER() OVER (ORDER BY cosine_distance(c2.embedding, q2.v)) AS rk
      FROM judian.chunks c2 JOIN judian_scratch.qvec q2 ON q2.id = 3
      ORDER BY cosine_distance(c2.embedding, q2.v) LIMIT 30) v ON v.id = c.id
LEFT JOIN (SELECT x.id AS id, ROW_NUMBER() OVER (ORDER BY MATCH(x.text) AGAINST('seekdb 怎么查看系统日志？' IN NATURAL LANGUAGE MODE) DESC) AS rk
      FROM judian.chunks x
      WHERE MATCH(x.text) AGAINST('seekdb 怎么查看系统日志？' IN NATURAL LANGUAGE MODE) LIMIT 30) f ON f.id = c.id
ORDER BY rrf DESC
LIMIT 5;
