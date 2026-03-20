from __future__ import annotations

import os
from typing import Optional
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .settings_service import get_setting, set_setting


JOB_ID = "b2_backup_guard_job"
CHECK_EVERY_SECONDS = 60
LAST_RUN_KEY = "backup_last_run_at"


def _parse_bool(raw: str | None) -> bool:
    s = (raw or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def get_backup_enabled() -> bool:
    return _parse_bool(os.environ.get("BACKUP_ENABLED") or get_setting("backup_enabled", "0"))


def get_backup_cron() -> str:
    # Priority: env > settings > default
    return (
        (os.environ.get("BACKUP_FREQUENCY_CRON") or "").strip()
        or (get_setting("backup_frequency_cron", "").strip() or "")
        or "0 3 * * *"
    )


def should_start_scheduler() -> bool:
    """
    Gunicorn with multiple workers will start the app multiple times.
    Only start scheduler on one worker to avoid duplicated backups.
    """
    worker_id = (os.environ.get("GUNICORN_WORKER_ID") or "").strip()
    if not worker_id:
        return True
    # Common: GUNICORN_WORKER_ID is "0", "1", ...
    return worker_id == "0"


def start_backup_scheduler(app) -> Optional[BackgroundScheduler]:
    if not should_start_scheduler():
        return None

    scheduler: BackgroundScheduler = BackgroundScheduler(timezone="UTC")
    app.extensions["backup_scheduler"] = scheduler
    reschedule_backup_job(app)
    scheduler.start()
    return scheduler


def reschedule_backup_job(app) -> None:
    scheduler: BackgroundScheduler | None = app.extensions.get("backup_scheduler")
    if not scheduler:
        # If scheduler isn't started yet, ignore.
        return

    # Always keep a lightweight guard job running.
    # It reads current settings each minute and only triggers backup when cron matches.
    try:
        old = scheduler.get_job(JOB_ID)
        if old:
            scheduler.remove_job(JOB_ID)
    except Exception:
        pass

    def _job():
        with app.app_context():
            try:
                if not get_backup_enabled():
                    return

                cron = get_backup_cron().strip()
                if not cron:
                    return

                cfg, missing = _load_cfg()
                if missing:
                    print(f"[BackupScheduler] skip: missing cfg {missing}")
                    return

                now = datetime.now(timezone.utc)
                now_min = now.replace(second=0, microsecond=0)

                # Prevent multiple runs within the same scheduled minute.
                last_run_raw = (get_setting(LAST_RUN_KEY, "") or "").strip()
                last_run_dt: datetime | None = None
                if last_run_raw:
                    try:
                        last_run_dt = datetime.fromisoformat(last_run_raw)
                        if last_run_dt.tzinfo is None:
                            last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        last_run_dt = None

                try:
                    trigger = CronTrigger.from_crontab(cron, timezone="UTC")
                except Exception as e:
                    print(f"[BackupScheduler] invalid cron '{cron}': {e}")
                    return

                # Compute the next fire time after (now_min - 1s).
                base = now_min - timedelta(seconds=1)
                prev = last_run_dt if last_run_dt else base
                next_fire = trigger.get_next_fire_time(prev, base)
                if not next_fire:
                    return

                # Only run when cron exactly matches current minute.
                next_fire_min = next_fire.astimezone(timezone.utc).replace(second=0, microsecond=0)
                if next_fire_min != now_min:
                    return

                # Skip if already ran for this minute (defensive).
                if last_run_dt and last_run_dt.replace(second=0, microsecond=0) == now_min:
                    return

                from ..routes.api.backup import _build_client, _write_items_csv
                import pathlib

                backup_path = pathlib.Path(cfg["source_path"])
                rows = _write_items_csv(backup_path)

                s3 = _build_client(cfg)
                s3.upload_file(str(backup_path), cfg["bucket_name"], cfg["target_key"])
                size = backup_path.stat().st_size
                t0 = datetime.now(timezone.utc)

                set_setting(LAST_RUN_KEY, now_min.isoformat())
                print(
                    f"[BackupScheduler] backup ok: rows={rows} size={size} key={cfg['target_key']} at={t0.isoformat()}"
                )
            except Exception as e:
                print(f"[BackupScheduler] guard job error: {e}")

    # Run every minute; the job itself decides whether it should back up now.
    scheduler.add_job(
        _job,
        trigger="interval",
        seconds=CHECK_EVERY_SECONDS,
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def _load_cfg():
    # Local wrapper to keep imports at runtime.
    from ..routes.api.backup import _load_cfg as _inner

    return _inner()

