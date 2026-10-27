"""回声酒馆后端配置（全部来自环境变量，便于评审复现）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """读取 dify/.env（如果存在），不覆盖已设置的环境变量。"""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - 启动期直接报错更清楚
        raise RuntimeError(f"{name} 必须是整数，当前值：{raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    # seekdb（兼容 MySQL 协议）
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    # 向量化服务（OpenAI 兼容 /v1/embeddings；默认用本机 Ollama）
    embed_base_url: str
    embed_api_key: str
    embed_model: str
    embed_dim: int
    embed_timeout: float
    embed_native: bool

    # 玩法参数
    turn_limit: int
    context_memories: int

    # 服务
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_host=_env("ECHO_DB_HOST", "127.0.0.1"),
            db_port=_env_int("ECHO_DB_PORT", 2881),
            db_user=_env("ECHO_DB_USER", "root"),
            db_password=_env("ECHO_DB_PASSWORD", ""),
            db_name=_env("ECHO_DB_NAME", "echo_tavern"),
            embed_base_url=_env("ECHO_EMBED_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/"),
            embed_api_key=_env("ECHO_EMBED_API_KEY", "ollama"),
            embed_model=_env("ECHO_EMBED_MODEL", "qwen3-embedding:0.6b"),
            embed_dim=_env_int("ECHO_EMBED_DIM", 1024),
            embed_timeout=float(_env("ECHO_EMBED_TIMEOUT", "60")),
            embed_native=_env("ECHO_EMBED_NATIVE", "0") in ("1", "true", "yes"),
            turn_limit=_env_int("ECHO_TURN_LIMIT", 6),
            context_memories=_env_int("ECHO_CONTEXT_MEMORIES", 5),
            host=_env("ECHO_HOST", "0.0.0.0"),
            port=_env_int("ECHO_PORT", 8765),
        )


_load_dotenv()
SETTINGS = Settings.from_env()
