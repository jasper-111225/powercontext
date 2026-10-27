"""《回声酒馆》FastAPI 服务。

Dify 只负责对话编排，所有“记忆/传播/结算”都在这里，落库到 seekdb。
"""

from __future__ import annotations

import html
import logging
from contextlib import asynccontextmanager

import pymysql
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import db
from .config import SETTINGS
from .embed import Embedder, EmbeddingError
from .game import build_finale, echo_payload, prepare_turn, commit_turn
from .personas import NPC_ORDER, npc_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("echo.api")

EMBEDDER = Embedder(SETTINGS)


class TurnRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=600)
    nickname: str = Field(default="", max_length=64)


class CommitRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    npc_id: str = Field(min_length=1, max_length=16)
    say: str = Field(default="", max_length=1200)
    retell: str = Field(default="", max_length=300)
    trust_delta: int = Field(default=0, ge=-5, le=5)
    mood: str = Field(default="curious", max_length=16)


class ResetRequest(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)


def _connect():
    return db.connect(SETTINGS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.init_schema(SETTINGS)
        log.info("seekdb schema ready: %s:%s/%s", SETTINGS.db_host, SETTINGS.db_port, SETTINGS.db_name)
    except Exception as exc:  # noqa: BLE001 - 启动失败时给评审清晰的提示
        log.error("seekdb 初始化失败（服务照常启动，/health 会显示原因）：%s", exc)
    yield
    EMBEDDER.close()


app = FastAPI(title="回声酒馆 Echo Tavern", version="1.0.0", lifespan=lifespan)


@app.get("/")
def index():
    return {
        "app": "回声酒馆 Echo Tavern",
        "tagline": "你说出口的秘密，会从别人嘴里长出来。",
        "endpoints": [
            "POST /api/v1/turn",
            "POST /api/v1/commit",
            "GET  /api/v1/echo/{player_id}",
            "GET  /api/v1/health",
            "GET  /wall/{player_id}",
        ],
        "turn_limit": SETTINGS.turn_limit,
    }


@app.get("/api/v1/health")
def health():
    result = {"ok": False, "seekdb": {}, "embedding": {}}
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION() AS v")
                row = cur.fetchone() or {}
                cur.execute(
                    "SELECT COUNT(*) AS n FROM information_schema.tables "
                    "WHERE table_schema=%s",
                    (SETTINGS.db_name,),
                )
                tables = int((cur.fetchone() or {}).get("n") or 0)
            result["seekdb"] = {
                "ok": True,
                "version": str(row.get("v") or ""),
                "database": SETTINGS.db_name,
                "tables": tables,
            }
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        result["seekdb"] = {"ok": False, "error": str(exc)}

    try:
        vec = EMBEDDER.embed_one("回声酒馆")
        result["embedding"] = {
            "ok": True,
            "model": SETTINGS.embed_model,
            "dim": len(vec),
            "base_url": SETTINGS.embed_base_url,
        }
    except EmbeddingError as exc:
        result["embedding"] = {"ok": False, "error": str(exc)}

    result["ok"] = bool(result["seekdb"].get("ok") and result["embedding"].get("ok"))
    return result


@app.get("/api/v1/session/{player_id}")
def session(player_id: str):
    conn = _connect()
    try:
        player, meta = db.ensure_player(conn, player_id)
        npcs = db.list_npcs(conn)
        return {
            "player": {
                "id": player_id,
                "turn": player["turn"],
                "phase": player["phase"],
                "round_no": player["round_no"],
            },
            "meta": meta,
            "npcs": [
                {
                    "id": n["id"],
                    "name": n["name"],
                    "title": n["title"],
                    "emoji": n["emoji"],
                    "greeting": n["greeting"],
                }
                for n in npcs
            ],
            "opening": "灰港的雨下了一整晚。回声酒馆里只剩三个人：老板娘罗莎、诗人阿烛、酒保老默。挑一个人，把你想传出去的秘密告诉他。",
        }
    finally:
        conn.close()


@app.post("/api/v1/turn")
def turn(req: TurnRequest):
    conn = _connect()
    try:
        return prepare_turn(
            conn,
            SETTINGS,
            EMBEDDER,
            player_id=req.player_id,
            message=req.message,
            nickname=req.nickname,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=503, detail=f"seekdb 错误：{exc}") from exc
    finally:
        conn.close()


@app.post("/api/v1/commit")
def commit(req: CommitRequest):
    conn = _connect()
    try:
        return commit_turn(
            conn,
            SETTINGS,
            EMBEDDER,
            player_id=req.player_id,
            npc_id=req.npc_id,
            say=req.say,
            retell=req.retell,
            trust_delta=req.trust_delta,
            mood=req.mood,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=503, detail=f"seekdb 错误：{exc}") from exc
    finally:
        conn.close()


@app.get("/api/v1/echo/{player_id}")
def echo(player_id: str, round_no: int | None = None):
    conn = _connect()
    try:
        payload = echo_payload(conn, player_id, round_no)
        secret_emb = db.round_secret_emb(conn, player_id, int(payload["round_no"]))
        if secret_emb is not None and payload["timeline"]:
            sims = db.round_similarities(
                conn, player_id, int(payload["round_no"]), secret_emb
            )
            for row in payload["timeline"]:
                dist = sims.get(row["id"])
                row["distance"] = round(dist, 4) if dist is not None else None
                row["fidelity"] = round(max(0.0, 1.0 - dist), 4) if dist is not None else None
            if payload["phase"] == "done":
                final = payload["timeline"][-1]
                payload["fidelity"] = final.get("fidelity")
                payload["distortion"] = (
                    round(1 - final["fidelity"], 4) if final.get("fidelity") is not None else None
                )
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/v1/reset")
def reset(req: ResetRequest):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for table in ("memories", "rumors", "npc_state"):
                cur.execute(f"DELETE FROM {table} WHERE player_id=%s", (req.player_id,))
            cur.execute("DELETE FROM players WHERE id=%s", (req.player_id,))
        return {"ok": True, "player_id": req.player_id, "message": "这一世的回声已经清空。"}
    finally:
        conn.close()


@app.get("/api/v1/finale/{player_id}")
def finale(player_id: str):
    conn = _connect()
    try:
        player = db.get_player(conn, player_id)
        if player is None:
            raise HTTPException(status_code=404, detail="玩家不存在")
        return build_finale(conn, SETTINGS, player, int(player["round_no"]))
    finally:
        conn.close()


@app.get("/wall/{player_id}", response_class=HTMLResponse)
def wall(player_id: str, round_no: int | None = None):
    conn = _connect()
    try:
        payload = echo_payload(conn, player_id, round_no)
        player = db.get_player(conn, player_id)
        assert player is not None
        secret_emb = db.round_secret_emb(conn, player_id, int(payload["round_no"]))
        sims: dict[int, float] = {}
        if secret_emb is not None:
            sims = db.round_similarities(conn, player_id, int(payload["round_no"]), secret_emb)
        return HTMLResponse(_render_wall(payload, sims, player))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


def _render_wall(payload: dict, sims: dict[int, float], player: dict) -> str:
    phase_cn = {"done": "已散场", "playing": "进行中"}.get(str(player.get("phase")), "进行中")
    cards = []
    for row in payload["timeline"]:
        dist = sims.get(row["id"])
        fid = max(0.0, 1.0 - dist) if dist is not None else None
        badge = "原始秘密" if row["is_secret"] else f"第 {row['generation']} 代"
        color = "#c26d5a" if row["speaker"] == "player" else "#6b8e9f"
        sim_text = f"保真 {fid * 100:.0f}%" if fid is not None else "起点"
        cards.append(
            f"""
            <div class="card" style="border-left-color:{color}">
              <div class="meta"><span class="badge">{badge}</span>
                <span class="route">{html.escape(row['speaker_name'])} → {html.escape(row['listener_name'])}</span>
                <span class="sim">{sim_text}</span></div>
              <p class="text">{html.escape(row['text'])}</p>
            </div>
            """
        )
    trusts = payload.get("trusts") or {}
    trust_html = "".join(
        f"<span class='chip'>{html.escape(npc_name(k))} {v}</span>" for k, v in trusts.items()
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>回声墙 · {html.escape(payload['player_id'])}</title>
<style>
  @page {{ size: 900px 1700px; margin: 0; }}
  body {{ margin:0; background:#14100e; color:#efe6d8; font-family:'Noto Serif SC',Georgia,serif; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:48px 20px 80px; }}
  h1 {{ font-size:30px; letter-spacing:4px; margin:0 0 8px; }}
  .sub {{ color:#a89a86; margin-bottom:28px; }}
  .card {{ background:#1e1814; border:1px solid #3a2f26; border-left:4px solid #3a2f26;
           border-radius:10px; padding:16px 18px; margin:14px 0; }}
  .meta {{ display:flex; gap:12px; align-items:center; font-size:13px; color:#a89a86; }}
  .badge {{ background:#3a2f26; color:#efe6d8; padding:2px 8px; border-radius:999px; }}
  .route {{ color:#c9b89f; }}
  .sim {{ margin-left:auto; color:#d9a066; }}
  .text {{ font-size:18px; line-height:1.7; margin:10px 0 2px; }}
  .chip {{ display:inline-block; border:1px solid #3a2f26; border-radius:999px;
           padding:4px 10px; margin:4px 8px 0 0; color:#c9b89f; font-size:13px; }}
  .foot {{ margin-top:32px; color:#6f6252; font-size:13px; }}
</style></head>
<body><div class="wrap">
  <h1>回声墙</h1>
  <div class="sub">第 {payload['round_no']} 局 · 第 {payload['turn']} 轮 · {phase_cn} · 共 {len(payload['timeline'])} 条回声</div>
  {''.join(cards) if cards else '<p class="sub">墙上还是空的。去酒馆说点什么。</p>'}
  <div style="margin-top:24px">{trust_html}</div>
  <p class="foot">seekdb 存储了每一条转述（rumors）与每一句对话（memories）；<br/>
  保真度由 cosine_distance(secret_emb, 该版本 emb) 计算。</p>
</div></body></html>"""
