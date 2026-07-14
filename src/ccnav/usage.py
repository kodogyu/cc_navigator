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
import math
import pathlib
import threading
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Callable, List, NamedTuple, Optional, Tuple

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
DEFAULT_TIMEOUT = 8.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects, because urllib would carry the token along.

    Its redirect handler copies every header except content-length/content-type onto
    the new request -- Authorization included, with no same-host check (requests, by
    contrast, strips it across hosts). So a single 302 would hand the account's bearer
    token to whatever host the Location names, over plain http if it says so. Returning
    None makes urllib raise the 3xx as an HTTPError, which fetch() already maps to a
    plain "could not fetch" message. The usage endpoint has no legitimate reason to
    redirect us. (tests/test_usage.py proves the leak against real sockets.)
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

# Failures the UI shows verbatim. Every one of them is a normal outcome, not a bug.
ERR_NO_CREDENTIALS = "로그인 정보를 찾을 수 없습니다"
# The access token lives ~8h and CLAUDE CODE refreshes it -- cc_navigator only reads it.
# So "log in again" is the wrong advice for an expired token: using any Claude Code
# session refreshes it. (We deliberately do NOT refresh it ourselves: the refresh token
# rotates, and consuming it without atomically persisting the new one could break the
# user's actual Claude Code login. Not a risk a status panel gets to take.)
ERR_EXPIRED = "인증 토큰이 만료되었습니다 — Claude Code 세션을 한 번 사용하면 갱신됩니다"
ERR_AUTH = "인증이 거부되었습니다 — 토큰 갱신(Claude Code 사용) 또는 claude 재로그인이 필요합니다"
ERR_RATE = "요청이 너무 많습니다 — 잠시 후 다시 시도하세요"
ERR_SERVER = "서버 오류 (%d) — 잠시 후 다시 시도하세요"
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
    expires_at: int = 0  # ms since epoch; 0 = the file did not say, so do not judge it


class Entry(NamedTuple):
    label: str
    percent: int
    severity: str
    resets_at: str


class Usage(NamedTuple):
    plan: str
    entries: List[Entry]


class UsageSection(NamedTuple):
    name: str
    usage: Optional[Usage]
    error: str


class UsageReport(NamedTuple):
    sections: List[UsageSection]


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
    expires = oauth.get("expiresAt")
    return Credentials(
        access_token=token,
        subscription_type=str(oauth.get("subscriptionType") or ""),
        rate_limit_tier=str(oauth.get("rateLimitTier") or ""),
        expires_at=expires if isinstance(expires, int) and not isinstance(expires, bool) else 0,
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


def _attempt(request, opener, timeout) -> Tuple[Optional[dict], str, bool]:
    """One try. Returns (payload, error, transient) -- `transient` says whether trying
    again could plausibly give a different answer."""
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # HTTPError is a URLError subclass, so it must be caught first. Report what
        # actually happened: a rate limit and a 500 are not the user's connection, and
        # calling them "(네트워크)" sent people to check their wifi for our problem.
        if exc.code in (401, 403):
            return None, ERR_AUTH, False       # a rejected token stays rejected
        if exc.code == 429:
            return None, ERR_RATE, False       # retrying immediately only digs deeper
        if 300 <= exc.code < 400:
            # A redirect we refused on purpose (_NoRedirect, so the token goes nowhere
            # else). Deterministic, so no retry; to the user it is simply a failed fetch.
            return None, ERR_NETWORK, False
        if exc.code >= 500:
            return None, ERR_SERVER % exc.code, True
        return None, ERR_SERVER % exc.code, False
    except (urllib.error.URLError, OSError):   # timeout, reset, DNS, offline
        return None, ERR_NETWORK, True
    except ValueError:                         # a body that is not JSON
        return None, ERR_SHAPE, False
    if not isinstance(payload, dict):
        return None, ERR_SHAPE, False
    return payload, "", False


def fetch(
    token: str,
    opener: Optional[Callable] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[Optional[dict], str]:
    """The only I/O: the same GET Claude Code itself makes. `opener` is injected so the
    tests never touch the network; it defaults to the redirect-refusing opener above,
    so the token cannot be carried anywhere else. Returns (payload, "") or (None, msg).

    A transient failure is retried ONCE. The user was already doing this by hand --
    "it said network, I pressed it again and it worked" -- so the retry was real, it was
    just being performed by a person. A 401 or a rate limit is not retried: the answer
    would be the same, only slower.
    """
    if opener is None:
        opener = _OPENER.open
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": OAUTH_BETA,
            "Content-Type": "application/json",
        },
    )
    payload, error, transient = _attempt(request, opener, timeout)
    if payload is None and transient:
        payload, error, _ = _attempt(request, opener, timeout)
    return payload, error


def load(
    read: Callable[[], Optional[Credentials]] = read_credentials,
    opener: Optional[Callable] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Tuple[Optional[Usage], str]:
    """The seam the UI calls (on a worker thread -- this does network I/O). Every failure
    comes back as a message to show, never as an exception."""
    credentials = read()
    if credentials is None:
        return None, ERR_NO_CREDENTIALS
    # Don't spend a request on a token whose own file says it is dead: the server would
    # just 401, and the honest advice for that is not "log in again" -- Claude Code
    # refreshes this token by itself the next time a session runs. expires_at == 0 means
    # the file did not say, so we send it and let the server decide.
    if credentials.expires_at and credentials.expires_at <= int(time.time() * 1000):
        return None, ERR_EXPIRED
    payload, error = fetch(credentials.access_token, opener=opener, timeout=timeout)
    if payload is None:
        return None, error
    parsed = parse(payload)
    if not parsed.entries:  # a 200 we cannot read is still a broken endpoint
        return None, ERR_SHAPE
    return Usage(plan=plan_name(credentials), entries=parsed.entries), ""


def load_report(
    claude_load: Callable = load,
    codex_load: Optional[Callable] = None,
) -> Tuple[UsageReport, str]:
    """Load Claude and Codex concurrently for the shared usage popover.

    Each provider keeps its own failure message, so one unavailable account
    never hides the other provider's useful limits.
    """
    if codex_load is None:
        from . import codexusage
        codex_load = codexusage.load

    loaders = (("Claude Code", claude_load), ("Codex", codex_load))
    results = [None, None]  # type: List[Optional[Tuple[Optional[Usage], str]]]

    def run_one(index: int, loader: Callable, name: str) -> None:
        try:
            results[index] = loader()
        except Exception as exc:  # noqa: BLE001 -- provider isolation is the contract
            results[index] = (None, "%s 사용량을 불러오지 못했습니다: %s" % (name, exc))

    threads = []
    for index, (name, loader) in enumerate(loaders):
        worker = threading.Thread(target=run_one, args=(index, loader, name), daemon=True)
        worker.start()
        threads.append(worker)
    for worker in threads:
        worker.join()

    sections = []  # type: List[UsageSection]
    for index, (name, _loader) in enumerate(loaders):
        result = results[index]
        if result is None:
            sections.append(UsageSection(name, None, "%s 사용량을 가져오지 못했습니다" % name))
        else:
            provider_usage, error = result
            sections.append(UsageSection(name, provider_usage, error))
    return UsageReport(sections), ""


# Optional local token-cost estimate -----------------------------------------
#
# This path is deliberately separate from load(): account limits are fetched
# only when the user presses the button, while ccusage is an external program
# that may read local Claude transcripts. Application gates every call behind
# Settings.ccusage_enabled. We never fall back to npx and never install anything.

WEEKLY_BUDGET_DOLLARS = 1315.0
CCUSAGE_TIMEOUT = 40.0
ERR_CCUSAGE_NOT_INSTALLED = (
    "ccusage를 찾을 수 없습니다. 외부 프로그램을 직접 설치한 뒤 다시 시도하세요."
)
ERR_CCUSAGE_FAILED = "ccusage 실행에 실패했습니다. 설치 상태와 권한을 확인하세요."
ERR_CCUSAGE_SHAPE = "ccusage 출력 형식을 알 수 없습니다. 버전 호환성을 확인하세요."


class TokenUsage(NamedTuple):
    token_cost: Optional[float]
    token_percent: Optional[float]
    error: str = ""


def week_start(today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=today.weekday())


def sum_since_monday(daily: List[dict], today: datetime.date) -> float:
    """Sum finite, non-negative daily costs from this Monday through today.

    ccusage output is external, untrusted input. Non-dicts, malformed dates,
    future rows and NaN/Infinity/negative costs are ignored rather than allowed
    to crash GTK or produce a misleading progress bar.
    """
    monday = week_start(today)
    total = 0.0
    for entry in daily:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("date") or entry.get("day") or ""
        try:
            day = datetime.date.fromisoformat(str(raw)[:10])
            cost = float(entry.get("totalCost"))
        except (TypeError, ValueError):
            continue
        if monday <= day <= today and math.isfinite(cost) and cost >= 0:
            total += cost
    return total


def parse_daily(text: str) -> Optional[List[dict]]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    days = data.get("daily") if isinstance(data, dict) else None
    return days if isinstance(days, list) else None


def ccusage_argv(
    which: Callable[[str], Optional[str]] = shutil.which,
) -> Optional[List[str]]:
    """Use only an already-installed executable resolved to an absolute path.

    There is intentionally no npx fallback: merely opening cc_navigator or
    enabling a display option must never download and execute remote code.
    """
    executable = which("ccusage")
    if not executable:
        return None
    return [str(pathlib.Path(executable).resolve()), "claude", "daily", "--json"]


def _run_ccusage(
    argv_for: Callable[[], Optional[List[str]]] = ccusage_argv,
) -> Tuple[Optional[str], str]:
    argv = argv_for()
    if not argv:
        return None, ERR_CCUSAGE_NOT_INSTALLED
    try:
        done = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=CCUSAGE_TIMEOUT,
            universal_newlines=True,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, ERR_CCUSAGE_FAILED
    if done.returncode != 0:
        return None, ERR_CCUSAGE_FAILED
    return done.stdout, ""


def fetch_token_usage(
    budget: float = WEEKLY_BUDGET_DOLLARS,
    today: Optional[datetime.date] = None,
    run: Callable[[], Tuple[Optional[str], str]] = _run_ccusage,
) -> TokenUsage:
    """Read the optional local estimate; never install, network-fetch, or raise."""
    try:
        text, error = run()
    except Exception:  # an injected/third-party runner is still untrusted
        return TokenUsage(None, None, ERR_CCUSAGE_FAILED)
    if text is None:
        return TokenUsage(None, None, error or ERR_CCUSAGE_FAILED)
    daily = parse_daily(text)
    if daily is None:
        return TokenUsage(None, None, ERR_CCUSAGE_SHAPE)
    today = today or datetime.date.today()
    cost = sum_since_monday(daily, today)
    try:
        valid_budget = math.isfinite(float(budget)) and float(budget) > 0
    except (TypeError, ValueError):
        valid_budget = False
    if not valid_budget:
        return TokenUsage(None, None, ERR_CCUSAGE_SHAPE)
    return TokenUsage(cost, cost / float(budget) * 100.0, "")
