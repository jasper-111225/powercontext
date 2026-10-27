"""seekdb 数据访问层（MySQL 协议 / pymysql）。

所有游戏状态都落在这里：玩家、NPC 信任、传闻传播链、对话记忆。
检索使用 seekdb 的向量距离函数 + 全文 MATCH ... AGAINST（IK 中文分词），
两路召回后用 RRF 融合。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from .config import Settings
from .personas import NPC_ORDER, NPCS

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


def connect(settings: Settings, *, with_db: bool = True) -> Connection:
    kwargs: dict[str, Any] = {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    }
    if with_db:
        kwargs["database"] = settings.db_name
    return pymysql.connect(**kwargs)


def vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


def init_schema(settings: Settings, *, reset: bool = False) -> None:
    if not _IDENT.match(settings.db_name):
        raise ValueError(f"非法库名：{settings.db_name}")
    conn = connect(settings, with_db=False)
    try:
        with conn.cursor() as cur:
            if reset:
                cur.execute(f"DROP DATABASE IF EXISTS `{settings.db_name}`")
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
            cur.execute(f"USE `{settings.db_name}`")
            schema = SCHEMA_PATH.read_text(encoding="utf-8").format(dim=settings.embed_dim)
            for statement in schema.split(";"):
                sql = statement.strip()
                if sql:
                    cur.execute(sql)
    finally:
        conn.close()
    seed_npcs(settings)


def seed_npcs(settings: Settings) -> None:
    conn = connect(settings)
    try:
        with conn.cursor() as cur:
            for npc in NPCS:
                cur.execute(
                    "REPLACE INTO npcs (id, name, title, emoji, color, persona, style, greeting, initial_trust) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        npc["id"],
                        npc["name"],
                        npc["title"],
                        npc["emoji"],
                        npc["color"],
                        npc["persona"],
                        npc["style"],
                        npc["greeting"],
                        npc["initial_trust"],
                    ),
                )
    finally:
        conn.close()


# --------------------------------------------------------------------------- 玩家


def get_player(conn: Connection, player_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM players WHERE id=%s", (player_id,))
        return cur.fetchone()


def create_player(conn: Connection, player_id: str, nickname: str = "") -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO players (id, nickname, turn, phase, round_no, last_seen) "
            "VALUES (%s,%s,0,'playing',1,NOW())",
            (player_id, nickname[:64]),
        )
        for npc in NPCS:
            cur.execute(
                "INSERT INTO npc_state (player_id, npc_id, trust, talks) VALUES (%s,%s,%s,0)",
                (player_id, npc["id"], npc["initial_trust"]),
            )
    player = get_player(conn, player_id)
    assert player is not None
    return player


def ensure_player(
    conn: Connection, player_id: str, nickname: str = ""
) -> tuple[dict[str, Any], dict[str, Any]]:
    """取玩家；第一次来就建档，上一局结束就开新的一局（记忆保留）。

    返回 (player, meta)，meta 里记录 returning / gap_minutes / new_round。
    """
    player = get_player(conn, player_id)
    meta = {"returning": False, "gap_minutes": 0, "new_round": False}
    if player is None:
        player = create_player(conn, player_id, nickname)
        return player, meta

    with conn.cursor() as cur:
        cur.execute(
            "SELECT TIMESTAMPDIFF(MINUTE, last_seen, NOW()) AS gap FROM players WHERE id=%s",
            (player_id,),
        )
        row = cur.fetchone() or {}
        gap = int(row.get("gap") or 0)
    meta["gap_minutes"] = gap
    if gap >= 30 and (int(player["turn"]) > 0 or player["phase"] == "done"):
        meta["returning"] = True

    if player["phase"] == "done" or int(player["turn"]) >= 1000:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE players SET round_no=round_no+1, turn=0, phase='playing', "
                "secret=NULL, secret_emb=NULL, last_npc='', last_seen=NOW() WHERE id=%s",
                (player_id,),
            )
            # 关系会随时间淡一点，但不会清零
            cur.execute(
                "UPDATE npc_state SET trust=GREATEST(20, trust-2), talks=0 WHERE player_id=%s",
                (player_id,),
            )
        meta["new_round"] = True
        meta["returning"] = True
        player = get_player(conn, player_id)
        assert player is not None
    else:
        with conn.cursor() as cur:
            cur.execute("UPDATE players SET last_seen=NOW() WHERE id=%s", (player_id,))
    return player, meta


def set_secret(conn: Connection, player_id: str, text: str, emb: list[float]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE players SET secret=%s, secret_emb=%s WHERE id=%s",
            (text, vec_literal(emb), player_id),
        )


def advance_turn(conn: Connection, player_id: str, npc_id: str, turn: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE players SET turn=%s, last_npc=%s, last_seen=NOW() WHERE id=%s",
            (turn, npc_id, player_id),
        )


def set_phase(conn: Connection, player_id: str, phase: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE players SET phase=%s, last_seen=NOW() WHERE id=%s", (phase, player_id))


# --------------------------------------------------------------------------- NPC 状态


def get_npc(conn: Connection, npc_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM npcs WHERE id=%s", (npc_id,))
        return cur.fetchone()


def list_npcs(conn: Connection) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM npcs ORDER BY FIELD(id, %s,%s,%s)", (*NPC_ORDER,))
        return list(cur.fetchall())


def get_npc_state(conn: Connection, player_id: str, npc_id: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trust, talks FROM npc_state WHERE player_id=%s AND npc_id=%s",
            (player_id, npc_id),
        )
        row = cur.fetchone()
        if row:
            return row
        initial = next((int(n["initial_trust"]) for n in NPCS if n["id"] == npc_id), 50)
        cur.execute(
            "INSERT INTO npc_state (player_id, npc_id, trust, talks) VALUES (%s,%s,%s,0)",
            (player_id, npc_id, initial),
        )
        return {"trust": initial, "talks": 0}


def all_npc_states(conn: Connection, player_id: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT npc_id, trust FROM npc_state WHERE player_id=%s",
            (player_id,),
        )
        return {str(r["npc_id"]): int(r["trust"]) for r in cur.fetchall()}


def update_trust(
    conn: Connection, player_id: str, npc_id: str, delta: int
) -> int:
    state = get_npc_state(conn, player_id, npc_id)
    trust = max(0, min(100, int(state["trust"]) + int(delta)))
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE npc_state SET trust=%s, talks=talks+1 WHERE player_id=%s AND npc_id=%s",
            (trust, player_id, npc_id),
        )
    return trust


# --------------------------------------------------------------------------- 传闻


def insert_rumor(
    conn: Connection,
    *,
    player_id: str,
    round_no: int,
    generation: int,
    parent_id: int | None,
    speaker: str,
    listener: str,
    text: str,
    emb: list[float],
    is_secret: bool,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rumors (player_id, round_no, generation, parent_id, speaker, listener, text, emb, is_secret) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                player_id,
                round_no,
                generation,
                parent_id,
                speaker,
                listener,
                text[:1000],
                vec_literal(emb),
                1 if is_secret else 0,
            ),
        )
        return int(cur.lastrowid)


def latest_heard(
    conn: Connection, player_id: str, npc_id: str, round_no: int
) -> dict[str, Any] | None:
    """这个 NPC 在本局听到的最后一个版本。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM rumors WHERE player_id=%s AND listener=%s AND round_no=%s "
            "ORDER BY id DESC LIMIT 1",
            (player_id, npc_id, round_no),
        )
        return cur.fetchone()


def latest_rumor(
    conn: Connection, player_id: str, round_no: int
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM rumors WHERE player_id=%s AND round_no=%s ORDER BY id DESC LIMIT 1",
            (player_id, round_no),
        )
        return cur.fetchone()


def timeline(conn: Connection, player_id: str, round_no: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM rumors WHERE player_id=%s AND round_no=%s ORDER BY id ASC",
            (player_id, round_no),
        )
        return list(cur.fetchall())


def cosine_to_secret(
    conn: Connection, rumor_id: int, secret_emb: list[float]
) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cosine_distance(emb,%s) AS d FROM rumors WHERE id=%s",
            (vec_literal(secret_emb), rumor_id),
        )
        row = cur.fetchone()
    if not row or row.get("d") is None:
        return None
    return float(row["d"])


# --------------------------------------------------------------------------- 记忆与检索


def insert_memory(
    conn: Connection,
    *,
    player_id: str,
    npc_id: str,
    round_no: int,
    turn: int,
    kind: str,
    text: str,
    emb: list[float],
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO memories (player_id, npc_id, round_no, turn, kind, text, emb) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (player_id, npc_id, round_no, turn, kind, text[:1000], vec_literal(emb)),
        )
        return int(cur.lastrowid)


def hybrid_memories(
    conn: Connection,
    *,
    player_id: str,
    npc_id: str,
    query: str,
    query_emb: list[float],
    limit: int = 5,
    pool: int = 12,
) -> list[dict[str, Any]]:
    """向量 + 全文双路召回，RRF 融合。

    向量路：seekdb `cosine_distance()` 精确计算（演示数据量小，保证可复现；
            emb 列上建有 HNSW 索引，数据量大时可切换 APPROXIMATE）。
    全文路：seekdb `MATCH ... AGAINST`（IK 中文分词）。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, text, kind, turn, round_no, created_at, "
            "cosine_distance(emb,%s) AS dist "
            "FROM memories WHERE player_id=%s AND npc_id=%s AND emb IS NOT NULL "
            "ORDER BY dist ASC LIMIT %s",
            (vec_literal(query_emb), player_id, npc_id, pool),
        )
        vector_rows = list(cur.fetchall())

        cur.execute(
            "SELECT id, text, kind, turn, round_no, created_at, "
            "MATCH(text) AGAINST(%s) AS rel "
            "FROM memories WHERE player_id=%s AND npc_id=%s "
            "AND MATCH(text) AGAINST(%s) ORDER BY rel DESC LIMIT %s",
            (query, player_id, npc_id, query, pool),
        )
        text_rows = list(cur.fetchall())

    scores: dict[int, float] = {}
    by_id: dict[int, dict[str, Any]] = {}
    for rank, row in enumerate(vector_rows):
        rid = int(row["id"])
        by_id[rid] = {**row, "via": "vector"}
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (60 + rank)
    for rank, row in enumerate(text_rows):
        rid = int(row["id"])
        if rid in by_id:
            by_id[rid]["via"] = "vector+fulltext"
            by_id[rid]["rel"] = row.get("rel")
        else:
            by_id[rid] = {**row, "via": "fulltext"}
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (60 + rank)

    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    results: list[dict[str, Any]] = []
    for rid, score in fused:
        row = by_id[rid]
        results.append({**row, "rrf": round(score, 6)})
    return results


def round_similarities(
    conn: Connection, player_id: str, round_no: int, secret_emb: Any
) -> dict[int, float]:
    """secret_emb 直接使用 VECTOR 列的原始文本（如 "[0.1,0.2,...]"）。"""
    raw = secret_emb.decode() if isinstance(secret_emb, (bytes, bytearray)) else str(secret_emb)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, cosine_distance(emb,%s) AS d FROM rumors "
            "WHERE player_id=%s AND round_no=%s",
            (raw, player_id, round_no),
        )
        return {int(r["id"]): float(r["d"]) for r in cur.fetchall() if r.get("d") is not None}


def round_secret_emb(conn: Connection, player_id: str, round_no: int) -> str | None:
    """取某一局最初那条秘密的向量（用于回看旧局，不能用玩家当前局的 secret_emb）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT emb FROM rumors WHERE player_id=%s AND round_no=%s AND is_secret=1 "
            "ORDER BY id ASC LIMIT 1",
            (player_id, round_no),
        )
        row = cur.fetchone()
    if not row or row.get("emb") is None:
        return None
    emb = row["emb"]
    return emb.decode() if isinstance(emb, (bytes, bytearray)) else str(emb)


def player_counts(conn: Connection, player_id: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM rumors WHERE player_id=%s AND is_secret=1",
            (player_id,),
        )
        secrets = int((cur.fetchone() or {}).get("n") or 0)
        cur.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE player_id=%s", (player_id,)
        )
        memories = int((cur.fetchone() or {}).get("n") or 0)
    return {"secrets": secrets, "memories": memories}


def now() -> datetime:
    return datetime.now()
