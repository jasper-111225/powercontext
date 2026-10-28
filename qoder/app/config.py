from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
_DB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _env(source: Mapping[str, str], name: str, default: str) -> str:
    value = source.get(name)
    return default if value is None or value == "" else value


def _env_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，当前为 {raw!r}") from exc


def _env_float(source: Mapping[str, str], name: str, default: float) -> float:
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字，当前为 {raw!r}") from exc


def _env_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def openai_url(base: str, path: str) -> str:
    """Join an Ollama base URL with an OpenAI-compatible path."""
    base = base.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/{path.lstrip('/')}"
    return f"{base}/v1/{path.lstrip('/')}"


@dataclass(frozen=True)
class Settings:
    root: Path
    db_host: str = "127.0.0.1"
    db_port: int = 2881
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "judian"

    embed_base: str = "http://127.0.0.1:11434"
    embed_model: str = "qwen3-embedding:0.6b"
    embed_dim: int = 1024
    embed_batch: int = 4
    embed_timeout: float = 180.0

    llm_base: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:0.5b"
    llm_timeout: float = 60.0
    llm_enabled: bool = True

    powercontext_url: str = "http://127.0.0.1:17430"
    powercontext_token: str = "judian-local-token"
    memory_timeout: float = 10.0

    host: str = "127.0.0.1"
    port: int = 8766
    user: str = "demo"
    evidence_top_n: int = 8
    recall_limit: int = 30
    rrf_k: int = 60
    min_vector_sim: float = 0.28
    min_coverage: float = 0.20
    max_upload_mb: int = 20
    log_queries: bool = True

    @property
    def corpus_dir(self) -> Path:
        return self.root / "data" / "corpus"

    @property
    def uploads_dir(self) -> Path:
        return self.root / "data" / "uploads"

    @property
    def gold_path(self) -> Path:
        return self.root / "data" / "gold.json"

    @property
    def runtime_dir(self) -> Path:
        return self.root / ".runtime"

    @property
    def static_dir(self) -> Path:
        return self.root / "app" / "static"

    @property
    def evidence_dir(self) -> Path:
        return self.root / "docs" / "evidence"

    @property
    def scope_cache_path(self) -> Path:
        return self.runtime_dir / "powercontext_scope.json"

    @property
    def memory_fallback_path(self) -> Path:
        return self.runtime_dir / "memory_fallback.json"

    @property
    def embed_cache_path(self) -> Path:
        return self.runtime_dir / "embed_cache.json"

    @property
    def scope_title(self) -> str:
        return "judian-learning"

    @property
    def profile_kind(self) -> str:
        return "judian-profile"

    @property
    def progress_kind(self) -> str:
        return "judian-progress"

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None, root: Path | None = None) -> "Settings":
        base = Path(root) if root is not None else ROOT
        if env is None:
            load_dotenv(base / ".env", override=False)
            env = os.environ

        db_name = _env(env, "JUDIAN_DB_NAME", "judian")
        if not _DB_NAME.match(db_name):
            raise ValueError(f"JUDIAN_DB_NAME 非法: {db_name!r}")

        settings = cls(
            root=base,
            db_host=_env(env, "JUDIAN_DB_HOST", "127.0.0.1"),
            db_port=_env_int(env, "JUDIAN_DB_PORT", 2881),
            db_user=_env(env, "JUDIAN_DB_USER", "root"),
            db_password=_env(env, "JUDIAN_DB_PASSWORD", ""),
            db_name=db_name,
            embed_base=_env(env, "JUDIAN_EMBED_BASE", "http://127.0.0.1:11434").rstrip("/"),
            embed_model=_env(env, "JUDIAN_EMBED_MODEL", "qwen3-embedding:0.6b"),
            embed_dim=_env_int(env, "JUDIAN_EMBED_DIM", 1024),
            embed_batch=_env_int(env, "JUDIAN_EMBED_BATCH", 4),
            embed_timeout=_env_float(env, "JUDIAN_EMBED_TIMEOUT", 180.0),
            llm_base=_env(env, "JUDIAN_LLM_BASE", "http://127.0.0.1:11434").rstrip("/"),
            llm_model=_env(env, "JUDIAN_LLM_MODEL", "qwen2.5:0.5b"),
            llm_timeout=_env_float(env, "JUDIAN_LLM_TIMEOUT", 60.0),
            llm_enabled=_env_bool(env, "JUDIAN_LLM_ENABLED", True),
            powercontext_url=_env(
                env, "JUDIAN_POWERCONTEXT_URL", "http://127.0.0.1:17430"
            ).rstrip("/"),
            powercontext_token=_env(env, "JUDIAN_POWERCONTEXT_TOKEN", "judian-local-token"),
            memory_timeout=_env_float(env, "JUDIAN_MEMORY_TIMEOUT", 10.0),
            host=_env(env, "JUDIAN_HOST", "127.0.0.1"),
            port=_env_int(env, "JUDIAN_PORT", 8766),
            user=_env(env, "JUDIAN_USER", "demo"),
            evidence_top_n=_env_int(env, "JUDIAN_EVIDENCE_TOP_N", 8),
            recall_limit=_env_int(env, "JUDIAN_RECALL_LIMIT", 30),
            rrf_k=_env_int(env, "JUDIAN_RRF_K", 60),
            min_vector_sim=_env_float(env, "JUDIAN_MIN_VECTOR_SIM", 0.28),
            min_coverage=_env_float(env, "JUDIAN_MIN_COVERAGE", 0.20),
            max_upload_mb=_env_int(env, "JUDIAN_MAX_UPLOAD_MB", 20),
            log_queries=_env_bool(env, "JUDIAN_LOG_QUERIES", True),
        )
        if settings.embed_dim <= 0:
            raise ValueError("JUDIAN_EMBED_DIM 必须为正数")
        if settings.evidence_top_n <= 0 or settings.recall_limit <= 0:
            raise ValueError("召回数量必须为正数")
        return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()
