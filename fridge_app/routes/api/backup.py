from __future__ import annotations

import csv
import os
import socket
import ssl
from typing import Any
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import boto3
import certifi
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from flask import Blueprint, jsonify

from ...extensions import db
from ...models import Item
from ...services.settings_service import get_secret_setting, get_setting
from ...utils.auth import admin_required


bp = Blueprint("backup_api", __name__, url_prefix="/backup")


def _err(code: str, message: str, status: int = 400):
    return jsonify({"success": False, "code": code, "message": message}), status


def _ok(message: str, **extra):
    payload = {"success": True, "message": message}
    payload.update(extra)
    return jsonify(payload)


def _load_cfg() -> tuple[dict[str, str], list[str]]:
    def _env_or_setting(env_key: str, setting_key: str, default: str = "") -> str:
        v = (os.environ.get(env_key) or "").strip()
        if v:
            return v
        return (get_setting(setting_key, default) or default).strip()

    cfg = {
        "endpoint": _env_or_setting("B2_ENDPOINT", "backup_b2_endpoint"),
        "access_key_id": get_secret_setting(setting_key="backup_b2_access_key_id", env_key="B2_ACCESS_KEY_ID"),
        "application_key": get_secret_setting(setting_key="backup_b2_application_key", env_key="B2_APPLICATION_KEY"),
        "bucket_name": _env_or_setting("B2_BUCKET_NAME", "backup_b2_bucket_name"),
        "region": _env_or_setting("B2_REGION", "backup_b2_region", "us-east-1") or "us-east-1",
        "source_path": _env_or_setting("BACKUP_SOURCE_PATH", "backup_source_path"),
        "target_key": _env_or_setting("BACKUP_TARGET_KEY", "backup_target_key", "latest_backup.csv") or "latest_backup.csv",
    }
    missing = [k for k in ("endpoint", "access_key_id", "application_key", "bucket_name", "source_path") if not cfg[k]]
    return cfg, missing


def _check_ssl(endpoint: str, timeout: int = 5) -> tuple[bool, str]:
    host = urlparse(endpoint).hostname or ""
    if not host:
        return False, "B2_ENDPOINT 格式不正确"
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host):
                pass
        return True, ""
    except Exception as e:
        return False, str(e)


def _build_client(cfg: dict[str, str]):
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["application_key"],
        region_name=cfg["region"],
        use_ssl=True,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _write_items_csv(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    items = Item.query.order_by(Item.created_at.asc(), Item.id.asc()).all()
    headers = [
        "name",
        "category",
        "quantity",
        "unit",
        "location",
        "note",
        "barcode",
        "shelf_life_days",
        "used_up",
        "created_at",
        "updated_at",
        "deleted_at",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for it in items:
            writer.writerow(
                {
                    "name": (it.name or "").strip(),
                    "category": (it.category or "").strip(),
                    "quantity": float(it.quantity or 0.0),
                    "unit": (it.unit or "").strip(),
                    "location": (it.location or "").strip(),
                    "note": (it.note or "").strip(),
                    "barcode": (it.barcode or "").strip() if it.barcode else "",
                    "shelf_life_days": it.shelf_life_days if it.shelf_life_days is not None else "",
                    "used_up": 1 if bool(it.used_up) else 0,
                    "created_at": it.created_at.isoformat() if it.created_at else "",
                    "updated_at": it.updated_at.isoformat() if it.updated_at else "",
                    "deleted_at": it.deleted_at.isoformat() if it.deleted_at else "",
                }
            )
    return len(items)


@bp.post("/check")
@admin_required
def backup_check():
    cfg, missing = _load_cfg()
    if missing:
        return _err("NO_CONFIG", f"缺少必填配置：{', '.join(missing)}", 403)

    tls_ok, tls_err = _check_ssl(cfg["endpoint"])
    if not tls_ok:
        return _err("NETWORK_ERROR", f"TLS 检查失败：{tls_err}", 400)

    try:
        s3 = _build_client(cfg)
        s3.head_bucket(Bucket=cfg["bucket_name"])
        return _ok("连接正常")
    except EndpointConnectionError as e:
        return _err("NETWORK_ERROR", str(e), 400)
    except ClientError as e:
        return _err("INVALID_CREDENTIALS", str(e), 400)
    except Exception as e:
        return _err("UNKNOWN_ERROR", str(e), 500)


@bp.post("/manual")
@admin_required
def backup_manual():
    cfg, missing = _load_cfg()
    if missing:
        return _err("NO_CONFIG", f"缺少必填配置：{', '.join(missing)}", 403)

    backup_path = Path(cfg["source_path"])
    try:
        rows = _write_items_csv(backup_path)
    except Exception as e:
        return _err("FILE_WRITE_ERROR", f"写入 CSV 失败：{e}", 500)

    if not backup_path.exists():
        return _err("FILE_NOT_FOUND", f"本地备份文件不存在：{backup_path}", 400)

    t0 = datetime.now(timezone.utc)
    try:
        s3 = _build_client(cfg)
        s3.upload_file(str(backup_path), cfg["bucket_name"], cfg["target_key"])
        size = backup_path.stat().st_size
        return _ok(
            "备份完成",
            backup_time=t0.isoformat(),
            size_bytes=size,
            rows=rows,
            object_key=cfg["target_key"],
        )
    except EndpointConnectionError as e:
        return _err("NETWORK_ERROR", str(e), 400)
    except ClientError as e:
        return _err("UPLOAD_FAILED", str(e), 400)
    except Exception as e:
        return _err("UNKNOWN_ERROR", str(e), 500)


@bp.post("/restore")
@admin_required
def backup_restore():
    cfg, missing = _load_cfg()
    if missing:
        return _err("NO_CONFIG", f"缺少必填配置：{', '.join(missing)}", 403)

    backup_path = Path(cfg["source_path"])
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        s3 = _build_client(cfg)
        s3.download_file(cfg["bucket_name"], cfg["target_key"], str(backup_path))
        size = backup_path.stat().st_size if backup_path.exists() else 0
        if not backup_path.exists() or size <= 0:
            return _err("FILE_NOT_FOUND", "恢复后本地 CSV 文件不存在或大小为 0。", 400)

        # Restore into DB (single-file strategy => exact snapshot restore).
        csv_text = backup_path.read_text(encoding="utf-8-sig", errors="ignore")
        # Parse as DictReader for robust column order.
        sample = (csv_text or "")[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(csv_text.splitlines(), dialect=dialect)

        def _parse_dt(v: Any) -> datetime | None:
            if v is None:
                return None
            s = str(v).strip()
            if not s:
                return None
            try:
                if s.endswith("Z"):
                    s = s[:-1]
                return datetime.fromisoformat(s)
            except Exception:
                return None

        def _to_float(v: Any, default: float = 0.0) -> float:
            try:
                return float(str(v).strip())
            except Exception:
                return default

        def _parse_used_up(v: Any) -> bool:
            s = str(v).strip().lower()
            if s in {"1", "true", "yes", "y"}:
                return True
            return False

        def _parse_shelf(v: Any) -> int | None:
            if v is None:
                return None
            s = str(v).strip()
            if not s or s == "null":
                return None
            try:
                return int(float(s))
            except Exception:
                return None

        # Clear & rebuild the snapshot (this is the restore semantics users expect).
        db.session.query(Item).delete(synchronize_session=False)
        now = datetime.utcnow()
        restored = 0
        for row in reader:
            if not isinstance(row, dict):
                continue
            name = str((row.get("name") or "")).strip()
            if not name:
                continue

            category = str((row.get("category") or "其他")).strip() or "其他"
            unit = str((row.get("unit") or "份")).strip() or "份"
            location = str((row.get("location") or "冰箱")).strip() or "冰箱"
            note = str((row.get("note") or "")).strip()
            barcode_raw = str((row.get("barcode") or "")).strip()
            barcode = barcode_raw if barcode_raw else None

            quantity = _to_float(row.get("quantity"), default=0.0)
            if quantity < 0:
                quantity = 0.0

            shelf_life_days_int = _parse_shelf(row.get("shelf_life_days"))
            used_up = _parse_used_up(row.get("used_up"))

            created_at = _parse_dt(row.get("created_at")) or now
            updated_at = _parse_dt(row.get("updated_at")) or created_at
            deleted_at = _parse_dt(row.get("deleted_at"))

            it = Item(
                name=name,
                category=category,
                quantity=quantity,
                unit=unit,
                location=location,
                note=note,
                barcode=barcode,
                shelf_life_days=shelf_life_days_int,
                used_up=used_up,
                deleted_at=deleted_at,
            )
            it.created_at = created_at
            it.updated_at = updated_at
            db.session.add(it)
            restored += 1

        db.session.commit()

        return _ok(
            "恢复完成（已覆盖当前库存数据）",
            restored_at=datetime.now(timezone.utc).isoformat(),
            size_bytes=size,
            rows=restored,
            object_key=cfg["target_key"],
        )
    except EndpointConnectionError as e:
        return _err("NETWORK_ERROR", str(e), 400)
    except ClientError as e:
        return _err("DOWNLOAD_FAILED", str(e), 400)
    except Exception as e:
        return _err("UNKNOWN_ERROR", str(e), 500)

