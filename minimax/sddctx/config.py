"""Configuration for the SDD context module.

All defaults point at local, disposable services so the demo runs on a clean
machine after ``scripts/setup_sddctx.sh``. Override with environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PACKAGE_ROOT / ".runtime"
EVIDENCE_DIR = PACKAGE_ROOT / "evidence"


@dataclass(frozen=True)
class Settings:
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    database: str
    powercontext_url: str
    powercontext_token: str
    query_log: Path
    scope_file: Path

    @property
    def mysql(self) -> dict:
        return {
            "host": self.mysql_host,
            "port": self.mysql_port,
            "user": self.mysql_user,
            "password": self.mysql_password,
            "charset": "utf8mb4",
            "autocommit": True,
            "connect_timeout": 10,
        }


def load_settings() -> Settings:
    return Settings(
        mysql_host=os.environ.get("SDDCTX_MYSQL_HOST", "127.0.0.1"),
        mysql_port=int(os.environ.get("SDDCTX_MYSQL_PORT", "2881")),
        mysql_user=os.environ.get("SDDCTX_MYSQL_USER", "root"),
        mysql_password=os.environ.get("SDDCTX_MYSQL_PASSWORD", ""),
        database=os.environ.get("SDDCTX_DATABASE", "minimax_sdd"),
        powercontext_url=os.environ.get("SDDCTX_POWERCONTEXT_URL", "http://127.0.0.1:17429"),
        powercontext_token=os.environ.get("SDDCTX_POWERCONTEXT_TOKEN", "minimax-sdd-local-token"),
        query_log=Path(os.environ.get("SDDCTX_QUERY_LOG", EVIDENCE_DIR / "logs" / "seekdb-queries.log")),
        scope_file=Path(os.environ.get("SDDCTX_SCOPE_FILE", RUNTIME_DIR / "scope.json")),
    )
