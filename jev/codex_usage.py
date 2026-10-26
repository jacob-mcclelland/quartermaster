"""Read the latest Codex rate-limit signal from local session rollouts.

Codex writes token-count events, with its rate-limit readings, into the
session logs under ~/.codex/sessions. This module keeps the newest reading,
caches it under logs/, and decides whether independent reviews should move
from the cross-family reviewer to a fresh-context reviewer. A missing, stale
or unreadable reading leaves that fallback off.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .client import LOG_DIR
from .config import load as _load_config


def _fallback_left() -> float:
    """Percent of the five-hour window left at which reviews fall back."""
    return _load_config().get("review_fallback_left_percent", 25)


def _sessions_root(root=None) -> Path:
    if root is not None:
        return Path(root)
    configured = os.environ.get("AGENT_CHECKS_CODEX_SESSIONS")
    return Path(configured) if configured else Path.home() / ".codex" / "sessions"


def _cache_path(cache=None) -> Path:
    if cache is not None:
        return Path(cache)
    configured = os.environ.get("AGENT_CHECKS_CODEX_USAGE_FILE")
    return Path(configured) if configured else LOG_DIR / "codex-usage.json"


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _token_reading(event: dict, source_file: Path) -> dict | None:
    if event.get("type") != "event_msg":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None

    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        limits = {}
    five_hour = weekly = None
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        minutes = window.get("window_minutes")
        if minutes == 300:
            five_hour = window
        elif minutes == 10080:
            weekly = window

    credits = limits.get("credits")
    if not isinstance(credits, dict):
        credits = {}
    return {
        "ts": event.get("timestamp"),
        "five_hour_used": _number(five_hour.get("used_percent")) if five_hour else None,
        "five_hour_resets_at": _number(five_hour.get("resets_at")) if five_hour else None,
        "weekly_used": _number(weekly.get("used_percent")) if weekly else None,
        "weekly_resets_at": _number(weekly.get("resets_at")) if weekly else None,
        "has_credits": credits.get("has_credits"),
        "reached_type": limits.get("rate_limit_reached_type"),
        "plan_type": limits.get("plan_type"),
        "limit_id": limits.get("limit_id"),
        "source_file": str(source_file),
    }


def latest(root=None, max_files=8) -> dict | None:
    """Return only the newest token-count rate-limit fields from recent rollouts."""
    try:
        files = []
        for path in _sessions_root(root).glob("*/*/*/rollout-*"):
            if not (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.zst")):
                continue
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
        files = [path for _, path in sorted(files, reverse=True)[:max(0, int(max_files))]]
    except Exception:  # noqa: BLE001 - usage discovery must fail open
        return None

    newest = None
    newest_time = None
    for path in files:
        try:
            raw = path.read_bytes()
            if path.name.endswith(".zst"):
                from compression import zstd

                raw = zstd.decompress(raw)
            contents = raw.decode("utf-8")
        except Exception:  # noqa: BLE001 - skip unreadable or malformed rollouts
            continue
        for line in contents.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            reading = _token_reading(event, path)
            event_time = _parse_time(reading.get("ts")) if reading else None
            if event_time is not None and (newest_time is None or event_time > newest_time):
                newest, newest_time = reading, event_time
    return newest


def refresh(root=None, cache=None):
    """Read current usage, cache it with a check time, and fail open."""
    try:
        reading = latest(root=root)
        if reading is None:
            return None
        record = {**reading, "checked_at": datetime.now(timezone.utc).isoformat()}
        path = _cache_path(cache)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return reading
    except Exception:  # noqa: BLE001 - usage discovery must fail open
        return None


def cached(max_age_s=900):
    """Return a recent cached reading, refreshing it when the cache is stale."""
    path = _cache_path()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        checked_at = _parse_time(record.get("checked_at")) if isinstance(record, dict) else None
        if checked_at is not None:
            age = (datetime.now(timezone.utc) - checked_at).total_seconds()
            if age <= max_age_s:
                return {key: value for key, value in record.items() if key != "checked_at"}
    except Exception:  # noqa: BLE001 - an absent or corrupt cache is just stale
        pass
    return refresh()


def review_fallback(reading, now=None) -> tuple[bool, str]:
    """Whether independent reviews should go to the fresh-context reviewer."""
    if not reading:
        return False, "no Codex usage reading"
    if now is None:
        now = datetime.now(timezone.utc)
    elif not isinstance(now, datetime):
        now = datetime.fromtimestamp(float(now), timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    observed_at = _parse_time(reading.get("ts"))
    if observed_at is not None and now - observed_at > timedelta(hours=24):
        return False, "Codex usage reading is over 24 hours old"
    reached_type = reading.get("reached_type")
    if reached_type:
        return True, f"Codex limit reached ({reached_type})"

    reset = _number(reading.get("five_hour_resets_at"))
    if reset is not None and datetime.fromtimestamp(reset, timezone.utc) < now:
        return False, "the 5-hour window has reset since the last reading"

    used = _number(reading.get("five_hour_used"))
    if used is not None:
        left = 100 - used
        on = left <= _fallback_left()
        return on, f"{left:.0f}% of the Codex 5-hour window left"
    return False, "no 5-hour reading"


def describe(reading) -> str:
    """Format the normalised signal as a single display line."""
    if not reading:
        return "Codex usage: unavailable"

    def window(label, used_key, reset_key):
        used = _number(reading.get(used_key))
        text = f"{used:.0f}% used" if used is not None else "unknown"
        reset = _number(reading.get(reset_key))
        if reset is not None:
            when = datetime.fromtimestamp(reset, timezone.utc).strftime("%H:%M UTC")
            text += f" (resets {when})"
        return f"{label} {text}"

    return "Codex usage: " + ", ".join((
        window("5-hour", "five_hour_used", "five_hour_resets_at"),
        window("weekly", "weekly_used", "weekly_resets_at"),
    ))
