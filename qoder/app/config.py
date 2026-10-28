"""据典 · 运行配置。

所有外部依赖都通过环境变量覆盖，默认值对应「本机 demo」：
seekdb 127.0.0.1:2881、Ollama 127.0.0.1:11434、PowerContext 127.0.0.1:17430。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RUNTIME_DIR = BASE_DIR / ".runtime"
STATIC_DIR = BASE_DIR / "app" / "static"


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # seekdb（MySQL 协议）
    db_host: str = field(default_factory=lambda: _env("JUDIAN_DB_HOST", "127.0.0.1"))
    db_port: int = field(default_factory=lambda: _env_int("JUDIAN_DB_PORT", 2881))
    db_user: str = field(default_factory=lambda: _env("JUDIAN_DB_USER", "root"))
    db_password: str = field(default_factory=lambda: _env("JUDIAN_DB_PASSWORD", ""))
    db_name: str = field(default_factory=lambda: _env("JUDIAN_DB_NAME", "judian"))

    # 向量模型（Ollama 的 OpenAI 兼容接口）
    embed_base: str = field(
        default_factory=lambda: _env("JUDIAN_EMBED_BASE", "http://127.0.0.1:11434/v1")
    )
    embed_model: str = field(
        default_factory=lambda: _env("JUDIAN_EMBED_MODEL", "qwen3-embedding:0.6b")
    )
    embed_dim: int = field(default_factory=lambda: _env_int("JUDIAN_EMBED_DIM", 1024))
    embed_batch: int = field(default_factory=lambda: _env_int("JUDIAN_EMBED_BATCH", 8))
    embed_cache: Path = field(
        default_factory=lambda: Path(_env("JUDIAN_EMBED_CACHE", str(RUNTIME_DIR / "embed_cache.jsonl")))
    )

    # 生成模型（Ollama 的 OpenAI 兼容接口；不可用时退化为抽取式回答）
    llm_base: str = field(
        default_factory=lambda: _env("JUDIAN_LLM_BASE", "http://127.0.0.1:11434/v1")
    )
    llm_model: str = field(default_factory=lambda: _env("JUDIAN_LLM_MODEL", "qwen2.5:0.5b"))
    llm_timeout: int = field(default_factory=lambda: _env_int("JUDIAN_LLM_TIMEOUT", 90))
    llm_max_tokens: int = field(default_factory=lambda: _env_int("JUDIAN_LLM_MAX_TOKENS", 420))

    # PowerContext（记忆）
    pc_url: str = field(
        default_factory=lambda: _env("JUDIAN_POWERCONTEXT_URL", "http://127.0.0.1:17430")
    )
    pc_token: str = field(
        default_factory=lambda: _env("JUDIAN_POWERCONTEXT_TOKEN", "judian-local-token")
    )
    pc_scope_title: str = field(
        default_factory=lambda: _env("JUDIAN_POWERCONTEXT_SCOPE", "judian-learning")
    )
    pc_scope_file: Path = field(
        default_factory=lambda: RUNTIME_DIR / "powercontext_scope.json"
    )

    # 服务
    host: str = field(default_factory=lambda: _env("JUDIAN_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("JUDIAN_PORT", 8766))
    user_id: str = field(default_factory=lambda: _env("JUDIAN_USER", "demo"))

    # 检索参数
    vector_top_k: int = field(default_factory=lambda: _env_int("JUDIAN_VECTOR_TOP_K", 30))
    fulltext_top_k: int = field(default_factory=lambda: _env_int("JUDIAN_FULLTEXT_TOP_K", 30))
    rrf_k: int = field(default_factory=lambda: _env_int("JUDIAN_RRF_K", 60))
    evidence_top_n: int = field(default_factory=lambda: _env_int("JUDIAN_EVIDENCE_TOP_N", 8))
    min_vector_similarity: float = field(
        default_factory=lambda: float(_env("JUDIAN_MIN_VECTOR_SIM", "0.28"))
    )

    def ensure_runtime(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
