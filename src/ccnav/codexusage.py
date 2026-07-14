"""Codex account limits through the local, authenticated app-server.

Unlike Claude Code's undocumented HTTP endpoint, Codex exposes account limits
through its own JSON-lines app-server protocol. Starting the local process lets
Codex use its existing login without cc_navigator reading, copying, or sending
credentials itself.
"""
from __future__ import annotations

import datetime
from typing import Callable, List, Optional, Sequence, Tuple

from . import __version__, proc
from .usage import Entry, Usage

DEFAULT_TIMEOUT = 12.0
ERR_UNAVAILABLE = "Codex 사용량을 가져오지 못했습니다"
ERR_LOGIN = "Codex 로그인 정보를 찾을 수 없습니다 — codex에서 로그인하세요"
ERR_SHAPE = "Codex 사용량 형식을 알 수 없습니다 (Codex 업데이트?)"


def _window_label(minutes, fallback: str) -> str:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        return fallback
    if minutes == 300:
        return "세션 (5시간)"
    if minutes == 24 * 60:
        return "일간"
    if minutes == 7 * 24 * 60:
        return "주간"
    if minutes % (24 * 60) == 0:
        return "%d일 한도" % (minutes // (24 * 60))
    if minutes % 60 == 0:
        return "%d시간 한도" % (minutes // 60)
    return "%d분 한도" % minutes


def _severity(percent: int) -> str:
    if percent >= 90:
        return "critical"
    if percent >= 75:
        return "warning"
    return "normal"


def _reset_iso(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    try:
        return datetime.datetime.fromtimestamp(
            value, tz=datetime.timezone.utc
        ).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _entry(window, fallback: str, prefix: str = "") -> Optional[Entry]:
    if not isinstance(window, dict):
        return None
    value = window.get("usedPercent")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    percent = int(value)
    label = _window_label(window.get("windowDurationMins"), fallback)
    if prefix:
        label = "%s · %s" % (prefix, label)
    return Entry(
        label=label,
        percent=percent,
        severity=_severity(percent),
        resets_at=_reset_iso(window.get("resetsAt")),
    )


def parse(payload) -> Usage:
    """Parse ``account/rateLimits/read``'s result into the shared UI model."""
    if not isinstance(payload, dict):
        return Usage("", [])

    by_id = payload.get("rateLimitsByLimitId")
    snapshots = []  # type: List[Tuple[str, dict]]
    if isinstance(by_id, dict):
        for limit_id, snapshot in by_id.items():
            if isinstance(snapshot, dict):
                snapshots.append((str(limit_id), snapshot))
    if not snapshots:
        legacy = payload.get("rateLimits")
        if isinstance(legacy, dict):
            snapshots.append((str(legacy.get("limitId") or "codex"), legacy))

    entries = []  # type: List[Entry]
    plan = ""
    multiple = len(snapshots) > 1
    for limit_id, snapshot in snapshots:
        if not plan:
            raw_plan = snapshot.get("planType")
            if isinstance(raw_plan, str) and raw_plan:
                plan = raw_plan.replace("_", " ").title()
        raw_name = snapshot.get("limitName") or limit_id
        prefix = str(raw_name) if multiple else ""
        primary = _entry(snapshot.get("primary"), "기본 한도", prefix)
        secondary = _entry(snapshot.get("secondary"), "보조 한도", prefix)
        if primary is not None:
            entries.append(primary)
        if secondary is not None:
            entries.append(secondary)
    return Usage(plan, entries)


def _messages() -> Sequence[dict]:
    return (
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "cc-navigator",
                    "title": "cc_navigator",
                    "version": __version__,
                },
                "capabilities": {"experimentalApi": True},
            },
        },
        {"method": "initialized"},
        {"id": 2, "method": "account/rateLimits/read", "params": None},
    )


def load(
    request: Callable = proc.request_json_line,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[Optional[Usage], str]:
    """Load Codex plan limits without exposing its credentials to this app."""
    code, response = request(
        ["codex", "app-server", "--stdio"], _messages(), 2,
        timeout=timeout, ready_id=1,
    )
    if code != 0 or not isinstance(response, dict):
        return None, ERR_UNAVAILABLE
    error = response.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "").lower()
        if "login" in message or "auth" in message or "credential" in message:
            return None, ERR_LOGIN
        return None, ERR_UNAVAILABLE
    result = response.get("result")
    parsed = parse(result)
    if not parsed.entries:
        return None, ERR_SHAPE
    return parsed, ""
