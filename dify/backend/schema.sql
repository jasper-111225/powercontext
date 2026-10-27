-- 回声酒馆 · seekdb schema（{dim} 由 ECHO_EMBED_DIM 注入）
-- 说明：
--   * ORGANIZATION = HEAP：seekdb 的堆表，支持向量/全文索引
--   * FULLTEXT ... WITH PARSER ik：中文分词
--   * VECTOR INDEX ... HNSW：为规模扩展预留；演示数据量下用精确距离保证可复现

CREATE TABLE IF NOT EXISTS players (
  id VARCHAR(64) PRIMARY KEY,
  nickname VARCHAR(64) DEFAULT '',
  secret TEXT,
  secret_emb VECTOR({dim}),
  turn INT NOT NULL DEFAULT 0,
  phase VARCHAR(16) NOT NULL DEFAULT 'playing',
  round_no INT NOT NULL DEFAULT 1,
  last_npc VARCHAR(16) DEFAULT '',
  last_seen DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ORGANIZATION = HEAP;

CREATE TABLE IF NOT EXISTS npcs (
  id VARCHAR(16) PRIMARY KEY,
  name VARCHAR(32) NOT NULL,
  title VARCHAR(64) NOT NULL,
  emoji VARCHAR(8) DEFAULT '',
  color VARCHAR(16) DEFAULT '',
  persona TEXT NOT NULL,
  style TEXT NOT NULL,
  greeting TEXT,
  initial_trust INT NOT NULL DEFAULT 50
) ORGANIZATION = HEAP;

CREATE TABLE IF NOT EXISTS npc_state (
  player_id VARCHAR(64) NOT NULL,
  npc_id VARCHAR(16) NOT NULL,
  trust INT NOT NULL DEFAULT 50,
  talks INT NOT NULL DEFAULT 0,
  PRIMARY KEY (player_id, npc_id)
) ORGANIZATION = HEAP;

CREATE TABLE IF NOT EXISTS rumors (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  player_id VARCHAR(64) NOT NULL,
  round_no INT NOT NULL DEFAULT 1,
  generation INT NOT NULL DEFAULT 0,
  parent_id BIGINT,
  speaker VARCHAR(16) NOT NULL,
  listener VARCHAR(16) NOT NULL,
  text TEXT NOT NULL,
  emb VECTOR({dim}),
  is_secret TINYINT NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_rumor_player (player_id, round_no),
  FULLTEXT INDEX ft_rumor (text) WITH PARSER ik,
  VECTOR INDEX vi_rumor (emb) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag)
) ORGANIZATION = HEAP;

CREATE TABLE IF NOT EXISTS memories (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  player_id VARCHAR(64) NOT NULL,
  npc_id VARCHAR(16) NOT NULL,
  round_no INT NOT NULL DEFAULT 1,
  turn INT NOT NULL DEFAULT 0,
  kind VARCHAR(16) NOT NULL,
  text TEXT NOT NULL,
  emb VECTOR({dim}),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_mem_player_npc (player_id, npc_id),
  FULLTEXT INDEX ft_mem (text) WITH PARSER ik,
  VECTOR INDEX vi_mem (emb) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag)
) ORGANIZATION = HEAP;
