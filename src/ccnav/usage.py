"""Two usage readings shown at the bottom of the panel:

* weekly_percent -- Claude's own weekly usage %, from the subscription usage API
  (/api/oauth/usage -> seven_day.utilization). The number Claude Code shows,
  displayed verbatim; resets Sunday.
* token_cost / token_percent -- this week's dollar spend, computed by `ccusage`
  from the local transcripts, summed from the current week's Monday, as a fraction
  of a fixed weekly budget.

Each is fetched independently and degrades to None on any failure (no token,
offline, ccusage missing, a schema change), so the panel just hides whatever is
unavailable rather than erroring. Both run on a background thread, never GTK's.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional

# The weekly budget the token bar is measured against (the subscription reports a
# utilisation % but no dollar limit, so this is a constant).
WEEKLY_BUDGET_DOLLARS = 1315.0

# ---- Claude weekly usage %, from the subscription API ------------------------

_CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")
_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_HEADERS = {
    "anthropic-beta": "oauth-2025-04-20",  # the endpoint 401s without it
    "anthropic-version": "2023-06-01",
    "Accept": "application/json",
    "User-Agent": "cc_navigator",
}
_HTTP_TIMEOUT = 15.0


def _access_token(path: str = _CREDENTIALS) -> Optional[str]:
    """The Claude OAuth access token, or None if missing/garbage. Claude Code
    keeps it fresh (it refreshes on its own); we only read it."""
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    token = (data.get("claudeAiOauth") or {}).get("accessToken")
    return token if isinstance(token, str) and token else None


def _http_get(token: str) -> str:
    req = urllib.request.Request(
        _USAGE_URL, headers=dict(_HEADERS, Authorization="Bearer " + token))
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def parse_weekly_percent(text: str) -> Optional[float]:
    """`seven_day.utilization` from the usage payload, or None on a bad/changed
    shape."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    seven = data.get("seven_day") if isinstance(data, dict) else None
    if not isinstance(seven, dict):
        return None
    try:
        return float(seven.get("utilization"))
    except (TypeError, ValueError):
        return None


def fetch_weekly_percent(
    token_reader: Callable[[], Optional[str]] = _access_token,
    http_get: Callable[[str], str] = _http_get,
) -> Optional[float]:
    """The weekly usage percent from the API, or None on any failure. Injectable
    so the logic is tested without a token or network."""
    token = token_reader()
    if not token:
        return None
    try:
        text = http_get(token)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return parse_weekly_percent(text)


# ---- token dollar spend this week, from ccusage ------------------------------

_CCUSAGE_TIMEOUT = 40.0


def week_start(today: datetime.date) -> datetime.date:
    """The Monday of `today`'s week. weekday() is 0 on Monday, so subtracting it
    lands on Monday; the plan resets Sunday night, so Monday begins the week."""
    return today - datetime.timedelta(days=today.weekday())


def sum_since_monday(daily: List[dict], today: datetime.date) -> float:
    """Sum totalCost over daily entries dated on/after this week's Monday. A row
    with an unparseable date or cost is skipped, never fatal."""
    monday = week_start(today)
    total = 0.0
    for entry in daily:
        raw = entry.get("date") or entry.get("day") or ""
        try:
            day = datetime.date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if day >= monday:
            try:
                total += float(entry.get("totalCost") or 0)
            except (TypeError, ValueError):
                continue
    return total


def parse_daily(text: str) -> Optional[List[dict]]:
    """The `daily` array from `ccusage ... daily --json`, or None on a bad shape."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    days = data.get("daily") if isinstance(data, dict) else None
    return days if isinstance(days, list) else None


def _ccusage_argv() -> Optional[List[str]]:
    exe = shutil.which("ccusage")
    if exe:
        return [exe, "claude", "daily", "--json"]
    npx = shutil.which("npx")
    if npx:
        return [npx, "-y", "ccusage", "claude", "daily", "--json"]
    return None


def _run_ccusage() -> Optional[str]:
    argv = _ccusage_argv()
    if not argv:
        return None
    try:
        done = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=_CCUSAGE_TIMEOUT, universal_newlines=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def token_cost_this_week(
    today: Optional[datetime.date] = None,
    run: Callable[[], Optional[str]] = _run_ccusage,
) -> Optional[float]:
    """This week's (Monday->today) dollar spend per ccusage, or None on failure."""
    text = run()
    if text is None:
        return None
    daily = parse_daily(text)
    if daily is None:
        return None
    if today is None:
        today = datetime.date.today()
    return sum_since_monday(daily, today)


# ---- combined snapshot -------------------------------------------------------

@dataclass(frozen=True)
class UsageSnapshot:
    weekly_percent: Optional[float]   # Claude API seven_day utilisation
    token_cost: Optional[float]       # ccusage this-week dollars
    token_percent: Optional[float]    # token_cost / budget * 100

    @property
    def empty(self) -> bool:
        return self.weekly_percent is None and self.token_cost is None


def fetch_usage(
    budget: float = WEEKLY_BUDGET_DOLLARS,
    today: Optional[datetime.date] = None,
    weekly: Callable[[], Optional[float]] = fetch_weekly_percent,
    token_cost: Callable[..., Optional[float]] = token_cost_this_week,
) -> UsageSnapshot:
    """Both readings at once; each independent, each None on its own failure."""
    weekly_percent = weekly()
    cost = token_cost(today=today)
    token_pct = (cost / budget * 100.0) if (cost is not None and budget > 0) else None
    return UsageSnapshot(weekly_percent=weekly_percent, token_cost=cost, token_percent=token_pct)
