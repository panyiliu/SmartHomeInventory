from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    instance_dir: Path
    database_uri: str
    secret_key: str


def load_config() -> AppConfig:
    base_dir = Path(__file__).resolve().parents[1]
    instance_dir = base_dir / "instance"
    instance_dir.mkdir(parents=True, exist_ok=True)

    db_path = instance_dir / "fridge.db"
    secret_key = (os.environ.get("SECRET_KEY") or "dev-secret-key-change-me").strip()
    database_uri = (os.environ.get("DATABASE_URL") or "").strip() or f"sqlite:///{db_path.as_posix()}"

    return AppConfig(
        base_dir=base_dir,
        instance_dir=instance_dir,
        database_uri=database_uri,
        secret_key=secret_key,
    )

