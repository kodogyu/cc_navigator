"""The logged-in account's plan limits, for the panel's usage button.

Claude Code's own `/usage` reads GET https://api.anthropic.com/api/oauth/usage with
the OAuth token in ~/.claude/.credentials.json; this asks the same question the same
way. That endpoint is UNDOCUMENTED and internal -- it can change or vanish with any
Claude Code release -- so every field here is treated as untrusted: `parse` is total
(a shape we do not recognise yields no entries, never an exception) and `load` maps
every failure to a message the panel can simply show. The panel must keep working
when this breaks.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import urllib.error
import urllib.request
from typing import Callable, List, NamedTuple, Optional, Tuple

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
DEFAULT_TIMEOUT = 8.0

# Failures the UI shows verbatim. Every one of them is a normal outcome, not a bug.
ERR_NO_CREDENTIALS = "로그인 정보를 찾을 수 없습니다"
ERR_AUTH = "인증이 만료되었습니다 — claude에서 다시 로그인하세요"
ERR_NETWORK = "사용량을 가져오지 못했습니다 (네트워크)"
ERR_SHAPE = "사용량 형식을 알 수 없습니다 (Claude Code 업데이트?)"

# limits[].kind -> the row label. An unknown kind keeps its raw name rather than being
# dropped: a new limit the endpoint starts reporting should still be visible.
_KIND_LABELS = {
    "session": "세션 (5시간)",
    "weekly_all": "주간 (전체)",
}


class Credentials(NamedTuple):
    access_token: str
    subscription_type: str
    rate_limit_tier: str


class Entry(NamedTuple):
    label: str
    percent: int
    severity: str
    resets_at: str


class Usage(NamedTuple):
    plan: str
    entries: List[Entry]


def credentials_path() -> pathlib.Path:
    return pathlib.Path.home() / ".claude" / ".credentials.json"


def read_credentials(path: Optional[pathlib.Path] = None) -> Optional[Credentials]:
    """The OAuth block of ~/.claude/.credentials.json, or None if it is missing,
    unreadable, or not the shape we expect. Never raises: a user who has not logged
    in must get a message, not a traceback."""
    path = path or credentials_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    oauth = raw.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    return Credentials(
        access_token=token,
        subscription_type=str(oauth.get("subscriptionType") or ""),
        rate_limit_tier=str(oauth.get("rateLimitTier") or ""),
    )


def plan_name(credentials: Optional[Credentials]) -> str:
    """A human plan name: "Max 20x" from the rate-limit tier when it names a multiple,
    else the capitalised subscription type ("Pro"), else nothing."""
    if credentials is None:
        return ""
    kind = (credentials.subscription_type or "").strip()
    tier = (credentials.rate_limit_tier or "").strip()
    if not kind:
        return ""
    name = kind.capitalize()
    # e.g. "default_claude_max_20x" -> the "20x" multiple, if it carries one.
    for part in tier.split("_"):
        if part.endswith("x") and part[:-1].isdigit():
            return "%s %s" % (name, part)
    return name


def _percent(value) -> Optional[int]:
    # bool is an int subclass; a True percent is nonsense, so exclude it.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _label_for(limit: dict) -> str:
    kind = str(limit.get("kind") or "")
    if kind in _KIND_LABELS:
        return _KIND_LABELS[kind]
    if kind == "weekly_scoped":
        scope = limit.get("scope")
        model = scope.get("model") if isinstance(scope, dict) else None
        name = model.get("display_name") if isinstance(model, dict) else None
        if name:
            return "주간 (%s)" % name
        return "주간 (모델별)"
    return kind


def parse(payload) -> Usage:
    """The limits worth showing, in the order the endpoint lists them. TOTAL: any shape
    we do not recognise yields no entries, so a changed endpoint degrades to a message
    instead of taking the panel down."""
    entries = []  # type: List[Entry]
    limits = payload.get("limits") if isinstance(payload, dict) else None
    if isinstance(limits, list):
        for limit in limits:
            if not isinstance(limit, dict):
                continue
            pct = _percent(limit.get("percent"))
            if pct is None:
                continue
            entries.append(Entry(
                label=_label_for(limit),
                percent=pct,
                severity=str(limit.get("severity") or "normal"),
                resets_at=str(limit.get("resets_at") or ""),
            ))
    return Usage(plan="", entries=entries)


def describe_reset(iso, now: Optional[datetime.datetime] = None) -> str:
    """"2시간 29분 후 리셋" within a day, "7월 19일 리셋" beyond one, "" if the value
    makes no sense (again: never raise on a field we do not control)."""
    if not isinstance(iso, str) or not iso:
        return ""
    text = iso[:-1] + "+00:00" if iso.endswith("Z") else iso  # 3.8 rejects a bare "Z"
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    delta = when - now
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "곧 리셋"
    if seconds >= 24 * 3600:
        local = when.astimezone()
        return "%d월 %d일 리셋" % (local.month, local.day)
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    if hours:
        return "%d시간 %d분 후 리셋" % (hours, minutes)
    return "%d분 후 리셋" % max(minutes, 1)


def fetch(
    token: str,
    opener: Callable = urllib.request.urlopen,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[Optional[dict], str]:
    """The only I/O: the same GET Claude Code itself makes. `opener` is injected so the
    tests never touch the network. Returns (payload, "") or (None, <message>)."""
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": OAUTH_BETA,
            "Content-Type": "application/json",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return None, (ERR_AUTH if exc.code in (401, 403) else ERR_NETWORK)
    except (urllib.error.URLError, OSError):
        return None, ERR_NETWORK
    except ValueError:  # a body that is not JSON -- the endpoint changed under us
        return None, ERR_SHAPE
    if not isinstance(payload, dict):
        return None, ERR_SHAPE
    return payload, ""


def load(
    read: Callable[[], Optional[Credentials]] = read_credentials,
    opener: Callable = urllib.request.urlopen,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[Optional[Usage], str]:
    """The seam the UI calls (on a worker thread -- this does network I/O). Every failure
    comes back as a message to show, never as an exception."""
    credentials = read()
    if credentials is None:
        return None, ERR_NO_CREDENTIALS
    payload, error = fetch(credentials.access_token, opener=opener, timeout=timeout)
    if payload is None:
        return None, error
    parsed = parse(payload)
    if not parsed.entries:  # a 200 we cannot read is still a broken endpoint
        return None, ERR_SHAPE
    return Usage(plan=plan_name(credentials), entries=parsed.entries), ""
