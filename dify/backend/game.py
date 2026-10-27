"""《回声酒馆》的玩法核心：选人、搭上下文、传播传闻、结算失真度。

设计要点
--------
* 传闻是一个带 `generation` 的链表：每一手转述都存成新节点（parent_id 指向上一个版本）。
* 每个 NPC 只知道“自己是 listener 的那条最新版本”，所以不同 NPC 的认知会分叉。
* 结算时用 seekdb 的 `cosine_distance(secret_emb, final_emb)` 算保真度，
  把“以讹传讹”变成可量化的游戏分数。
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from typing import Any

from pymysql.connections import Connection

from . import db
from .config import Settings
from .embed import Embedder
from .personas import NPC_BY_ID, NPC_ORDER, npc_name, next_npc_id

log = logging.getLogger("echo.game")

ALIASES: dict[str, tuple[str, ...]] = {
    "rosa": ("罗莎", "老板娘", "老板"),
    "azhu": ("阿烛", "诗人", "小烛"),
    "momo": ("老默", "酒保", "默叔"),
}

MOODS = {"amused", "suspicious", "moved", "annoyed", "curious"}

RETRY_HINT = "对方好像没听清。你可以换个说法，或者直接点名叫住某个人。"


def resolve_npc(message: str, last_npc: str = "") -> str:
    """消息里点到谁，就找谁；没点就沿用上一次，再不行找罗莎。"""
    best: tuple[int, int, str] | None = None
    for npc_id, names in ALIASES.items():
        for alias in names:
            pos = message.find(alias)
            if pos < 0:
                continue
            candidate = (pos, -len(alias), npc_id)
            if best is None or candidate < best:
                best = candidate
    if best is not None:
        return best[2]
    if last_npc in NPC_BY_ID:
        return last_npc
    return NPC_ORDER[0]


def clean_message(message: str) -> str:
    text = message.strip()
    for names in ALIASES.values():
        for alias in sorted(names, key=len, reverse=True):
            for sep in ("：", ":", "，", ",", " ", "　"):
                prefix = alias + sep
                if text.startswith(prefix):
                    text = text[len(prefix) :].strip()
                    break
            else:
                continue
            break
    text = text.strip().strip("“”\"'")
    for pattern in (
        r"^我(?:要|想)?(?:告诉你|跟你说|跟你讲|和你说)(?:一个|个)?秘密[：:,，]?\s*",
        r"^告诉你(?:一个|个)?秘密[：:,，]?\s*",
        r"^我跟你说[，,:：]?\s*",
        r"^你知道吗[，,]?\s*",
        r"^听说了吗[，,]?\s*",
    ):
        text = re.sub(pattern, "", text)
    return text.strip() or message.strip()


def _speaker_name(speaker: str) -> str:
    if speaker == "player":
        return "你"
    return npc_name(speaker)


def _fmt_trust(trust: int) -> str:
    if trust >= 75:
        return f"{trust}/100（很信任你）"
    if trust >= 55:
        return f"{trust}/100（愿意听你说）"
    if trust >= 35:
        return f"{trust}/100（半信半疑）"
    return f"{trust}/100（防着你）"


def build_context(
    *,
    npc: dict[str, Any],
    trust: int,
    turn: int,
    turn_limit: int,
    message: str,
    memories: list[dict[str, Any]],
    heard: dict[str, Any] | None,
    first_secret: bool,
    returning: bool,
    new_round: bool,
    last_round_echo: dict[str, Any] | None,
    gap_minutes: int,
) -> str:
    lines: list[str] = []
    lines.append("【酒馆】灰港，雨夜。壁炉快灭了，窗外有船进港的钟声。")
    lines.append(
        f"【这一局】第 {min(turn, turn_limit)}/{turn_limit} 轮；"
        f"{npc['name']} 对你的信任：{_fmt_trust(trust)}。"
    )

    if returning:
        if new_round:
            lines.append(
                "【上次来访】你们上一局见过。墙上的回声还没有散，"
                "而这个人今晚又带着新的秘密坐了下来。"
            )
            if last_round_echo:
                lines.append(
                    f"【墙上残留的回声】上一局最后传成：「{last_round_echo['text']}」。"
                )
        else:
            lines.append(
                f"【上次来访】大约 {max(gap_minutes, 1)} 分钟前，你们聊到一半。"
                "你记得这个人上次留下的语气。"
            )

    if first_secret:
        lines.append("【刚刚发生】玩家第一次开口，把一件从没对别人说过的事告诉了你。")
    else:
        lines.append(f"【刚刚发生】玩家对你说：「{message}」。")

    if memories:
        lines.append("【你记得的】")
        for idx, mem in enumerate(memories, start=1):
            label = "玩家说过" if mem["kind"] == "player_said" else "你说过"
            when = "本局" if mem.get("round_no") == mem.get("_round") else "以前"
            lines.append(f"{idx}.（{when}·{label}）{mem['text']}")
    else:
        lines.append("【你记得的】这个人以前没怎么来过，或者你一时想不起。")

    if heard is not None and not first_secret:
        from_whom = "玩家亲口" if heard.get("is_secret") else _speaker_name(str(heard["speaker"]))
        lines.append(
            f"【你最近听来的传闻】第 {heard['generation']} 代，从{from_whom}那里"
            f"听来：「{heard['text']}」。"
        )

    lines.append(
        "【提醒】你只知道上面这些，不要假装知道别的；转述时按你的性格添油加醋。"
    )
    return "\n".join(lines)


def prepare_turn(
    conn: Connection,
    settings: Settings,
    embedder: Embedder,
    *,
    player_id: str,
    message: str,
    nickname: str = "",
) -> dict[str, Any]:
    message = message.strip()
    if not message:
        raise ValueError("消息不能为空")
    player, meta = db.ensure_player(conn, player_id, nickname)
    npc_id = resolve_npc(message, str(player.get("last_npc") or ""))
    npc = db.get_npc(conn, npc_id)
    if npc is None:
        raise ValueError(f"NPC 不存在：{npc_id}")
    state = db.get_npc_state(conn, player_id, npc_id)
    round_no = int(player["round_no"])

    first_secret = not player.get("secret")
    clean = clean_message(message)
    secret_text = str(player.get("secret") or "")
    if first_secret:
        emb = embedder.embed_one(clean)
        secret_text = clean
        db.set_secret(conn, player_id, secret_text, emb)
        db.insert_rumor(
            conn,
            player_id=player_id,
            round_no=round_no,
            generation=0,
            parent_id=None,
            speaker="player",
            listener=npc_id,
            text=secret_text,
            emb=emb,
            is_secret=True,
        )
        player["secret"] = secret_text
        query_emb = emb
    else:
        query_emb = embedder.embed_one(message)

    # 先检索再写入，避免“当前这句话”霸占记忆榜
    memories = db.hybrid_memories(
        conn,
        player_id=player_id,
        npc_id=npc_id,
        query=message,
        query_emb=query_emb,
        limit=settings.context_memories,
    )
    for mem in memories:
        mem["_round"] = round_no
    db.insert_memory(
        conn,
        player_id=player_id,
        npc_id=npc_id,
        round_no=round_no,
        turn=int(player["turn"]) + 1,
        kind="player_said",
        text=clean,
        emb=query_emb,
    )

    heard = None if first_secret else db.latest_heard(conn, player_id, npc_id, round_no)
    last_round_echo = None
    if meta["new_round"] and round_no > 1:
        last_round_echo = db.latest_rumor(conn, player_id, round_no - 1)

    context = build_context(
        npc=npc,
        trust=int(state["trust"]),
        turn=int(player["turn"]) + 1,
        turn_limit=settings.turn_limit,
        message=clean,
        memories=memories,
        heard=heard,
        first_secret=first_secret,
        returning=bool(meta["returning"]),
        new_round=bool(meta["new_round"]),
        last_round_echo=last_round_echo,
        gap_minutes=int(meta["gap_minutes"]),
    )

    debug = {
        "player_id": player_id,
        "round_no": round_no,
        "npc": npc_id,
        "first_secret": first_secret,
        "retrieval": [
            {"id": m["id"], "via": m.get("via"), "rrf": m.get("rrf"), "text": m["text"][:40]}
            for m in memories
        ],
    }
    log.info("turn %s", json.dumps(debug, ensure_ascii=False))

    return {
        "player_id": player_id,
        "round_no": round_no,
        "turn": int(player["turn"]) + 1,
        "turn_limit": settings.turn_limit,
        "phase": player["phase"],
        "secret": str(player.get("secret") or secret_text),
        "first_secret": first_secret,
        "returning": bool(meta["returning"]),
        "npc": {
            "id": npc["id"],
            "name": npc["name"],
            "title": npc["title"],
            "emoji": npc["emoji"],
            "persona": npc["persona"],
            "style": npc["style"],
            "trust": int(state["trust"]),
        },
        "context_text": context,
        "hint": "用一句话回应，或者点名另一个人，把传闻传过去。",
    }


FALLBACK_TAILS = (
    "——这事要是真的，今晚的酒钱得翻倍。",
    "，像一颗不肯落地的星，砸在酒杯里。",
    "。嗯。别声张。",
)


def _core_text(text: str) -> str:
    """去掉兜底尾巴，避免每一代都在上一代后面无限接龙。"""
    core = text.strip()
    for tail in FALLBACK_TAILS:
        if tail in core:
            core = core.split(tail)[0]
    return core.strip()[:120]


def _fallback_retell(heard_text: str, npc_id: str, heard_is_secret: bool) -> str:
    base = _core_text(heard_text)
    if npc_id == "rosa":
        return f"{base}——这事要是真的，今晚的酒钱得翻倍。"
    if npc_id == "azhu":
        return f"{base}，像一颗不肯落地的星，砸在酒杯里。"
    return f"{base}。嗯。别声张。"


def normalize_retell(
    retell: str, *, npc_id: str, heard: dict[str, Any] | None
) -> str:
    text = (retell or "").strip().strip("“”\"'")
    if not text:
        heard_text = str((heard or {}).get("text") or "那个秘密")
        return _fallback_retell(heard_text, npc_id, bool((heard or {}).get("is_secret")))
    text = " ".join(text.split())[:220]
    heard_text = str((heard or {}).get("text") or "").strip()
    if heard_text and _core_text(text) == _core_text(heard_text):
        extra = {
            "rosa": "我亲眼见过账本，利息都算不清了",
            "azhu": "像一颗不肯落地的星，砸在酒杯里",
            "momo": "账我记着，数目对不上",
        }.get(npc_id, "我发誓")
        text = f"{_core_text(text)}，{extra}。"
    return text


def commit_turn(
    conn: Connection,
    settings: Settings,
    embedder: Embedder,
    *,
    player_id: str,
    npc_id: str,
    say: str,
    retell: str,
    trust_delta: int,
    mood: str,
) -> dict[str, Any]:
    player = db.get_player(conn, player_id)
    if player is None:
        raise ValueError("玩家不存在，请先发一条消息")
    npc = db.get_npc(conn, npc_id)
    if npc is None:
        raise ValueError(f"NPC 不存在：{npc_id}")
    round_no = int(player["round_no"])

    say = " ".join((say or "").split())[:800]
    if not say:
        say = "（对方看了你一眼，没有马上接话。）"

    if player["phase"] == "done":
        return build_finale(conn, settings, player, round_no)

    heard = db.latest_heard(conn, player_id, npc_id, round_no)
    if heard is None:
        heard = db.latest_rumor(conn, player_id, round_no)
    text = normalize_retell(retell, npc_id=npc_id, heard=heard)
    emb = embedder.embed_one(text)
    generation = int((heard or {}).get("generation") or 0) + 1
    listener = next_npc_id(npc_id)
    rumor_id = db.insert_rumor(
        conn,
        player_id=player_id,
        round_no=round_no,
        generation=generation,
        parent_id=int(heard["id"]) if heard else None,
        speaker=npc_id,
        listener=listener,
        text=text,
        emb=emb,
        is_secret=False,
    )

    db.insert_memory(
        conn,
        player_id=player_id,
        npc_id=npc_id,
        round_no=round_no,
        turn=int(player["turn"]) + 1,
        kind="npc_said",
        text=say,
        emb=embedder.embed_one(say),
    )

    delta = max(-5, min(5, int(trust_delta or 0)))
    trust = db.update_trust(conn, player_id, npc_id, delta)
    turn = int(player["turn"]) + 1
    mood = mood if mood in MOODS else "curious"

    status_line = (
        f"🍷 第 {min(turn, settings.turn_limit)}/{settings.turn_limit} 轮 · "
        f"{npc['name']} 信任 {trust} · 传闻到第 {generation} 代 · "
        f"下一站：{npc_name(listener)}"
    )

    if turn >= settings.turn_limit:
        db.set_phase(conn, player_id, "done")
        db.advance_turn(conn, player_id, npc_id, turn)
        player = db.get_player(conn, player_id)
        assert player is not None
        finale = build_finale(conn, settings, player, round_no)
        finale.update(
            {
                "stored_rumor_id": rumor_id,
                "listener": listener,
                "mood": mood,
                "say": say,
                "retell": text,
                "trust": trust,
            }
        )
        return finale

    db.advance_turn(conn, player_id, npc_id, turn)
    return {
        "player_id": player_id,
        "phase": "playing",
        "turn": turn,
        "turn_limit": settings.turn_limit,
        "status_line": status_line,
        "generation": generation,
        "stored_rumor_id": rumor_id,
        "listener": listener,
        "listener_name": npc_name(listener),
        "trust": trust,
        "trust_delta": delta,
        "mood": mood,
        "say": say,
        "retell": text,
        "finale_context": "",
        "verdict": "",
        "fidelity": None,
        "distortion": None,
        "score": None,
        "rank": "",
        "timeline": [],
    }


VERDICTS = {
    "S": "墙把这句话原样还给了你。灰港的夜风都替你保密。",
    "A": "只歪了一点，像一件被穿旧的外套——还是你的，只是有了别人的体温。",
    "B": "故事已经自己长出了脚。你认得出它，但它不太认你了。",
    "C": "你听见的那句话，已经和你说的没有关系了。墙比你更会讲故事。",
}


def _rank(score: int) -> str:
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    return "C"


def build_finale(
    conn: Connection,
    settings: Settings,
    player: dict[str, Any],
    round_no: int,
) -> dict[str, Any]:
    secret = str(player.get("secret") or "")
    chain = db.timeline(conn, str(player["id"]), round_no)
    final = chain[-1] if chain else None
    trusts = db.all_npc_states(conn, str(player["id"]))

    dist: float | None = None
    secret_emb_raw = player.get("secret_emb")
    if secret_emb_raw is not None and final is not None:
        raw = secret_emb_raw.decode() if isinstance(secret_emb_raw, (bytes, bytearray)) else str(secret_emb_raw)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cosine_distance(emb, %s) AS d FROM rumors WHERE id=%s",
                (raw, int(final["id"])),
            )
            row = cur.fetchone()
        if row and row.get("d") is not None:
            dist = float(row["d"])
    fidelity = max(0.0, min(1.0, 1.0 - dist)) if dist is not None else 0.0
    spread_ids = {str(r["listener"]) for r in chain if str(r["listener"]) in NPC_ORDER}
    spread = len(spread_ids) / len(NPC_ORDER)
    avg_trust = statistics.mean(trusts.values()) if trusts else 50.0
    score = int(round(100 * (0.55 * fidelity + 0.25 * spread + 0.20 * avg_trust / 100)))
    rank = _rank(score)
    verdict = VERDICTS[rank]

    lines = [
        f"【原始秘密】{secret}",
        (
            f"【最终回声】第 {final['generation']} 代，最后停在 {npc_name(str(final['listener']))} 那里："
            f"「{final['text']}」"
            if final
            else "【最终回声】这句话还没传出去。"
        ),
        f"【保真度】{fidelity * 100:.0f}%（失真度 {100 - fidelity * 100:.0f}%）",
        f"【传播链】",
    ]
    for row in chain:
        lines.append(
            f"- 第 {row['generation']} 代：{_speaker_name(str(row['speaker']))} → "
            f"{npc_name(str(row['listener']))}：{row['text']}"
        )
    lines.append(
        "【信任】" + "；".join(f"{npc_name(k)} {v}" for k, v in trusts.items())
    )
    finale_context = "\n".join(lines)

    timeline_rows = [
        {
            "id": int(r["id"]),
            "generation": int(r["generation"]),
            "speaker": str(r["speaker"]),
            "speaker_name": _speaker_name(str(r["speaker"])),
            "listener": str(r["listener"]),
            "listener_name": npc_name(str(r["listener"])),
            "text": str(r["text"]),
            "is_secret": bool(r["is_secret"]),
        }
        for r in chain
    ]

    return {
        "player_id": str(player["id"]),
        "phase": "done",
        "turn": int(player["turn"]),
        "turn_limit": settings.turn_limit,
        "round_no": round_no,
        "status_line": (
            f"🌙 本局结束 · 保真度 {fidelity * 100:.0f}% · 评分 {score}（{rank}）"
        ),
        "generation": int(final["generation"]) if final else 0,
        "listener": str(final["listener"]) if final else "",
        "listener_name": npc_name(str(final["listener"])) if final else "",
        "trust": trusts,
        "trust_delta": 0,
        "mood": "moved",
        "finale_context": finale_context,
        "verdict": verdict,
        "fidelity": round(fidelity, 4),
        "distortion": round(1 - fidelity, 4),
        "score": score,
        "rank": rank,
        "secret": secret,
        "final_text": str(final["text"]) if final else "",
        "timeline": timeline_rows,
        "spread": round(spread, 2),
        "trusts": trusts,
    }


def echo_payload(
    conn: Connection, player_id: str, round_no: int | None = None
) -> dict[str, Any]:
    player = db.get_player(conn, player_id)
    if player is None:
        raise ValueError("玩家不存在")
    rn = round_no or int(player["round_no"])
    chain = db.timeline(conn, player_id, rn)
    secret = None
    for row in chain:
        if row["is_secret"]:
            secret = row
            break
    trusts = db.all_npc_states(conn, player_id)
    return {
        "player_id": player_id,
        "round_no": rn,
        "phase": player["phase"],
        "turn": int(player["turn"]),
        "secret": str(secret["text"]) if secret else None,
        "timeline": [
            {
                "id": int(r["id"]),
                "generation": int(r["generation"]),
                "speaker": str(r["speaker"]),
                "speaker_name": _speaker_name(str(r["speaker"])),
                "listener": str(r["listener"]),
                "listener_name": npc_name(str(r["listener"])),
                "text": str(r["text"]),
                "is_secret": bool(r["is_secret"]),
            }
            for r in chain
        ],
        "trusts": trusts,
    }
