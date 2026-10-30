#!/usr/bin/env python3
"""Render the GitHub contribution streak card as dark/light SVGs.

Standard library only: no pip installs, no third-party code at runtime.
The contribution calendar already counts private contributions when the
profile shows them, so the workflow's own token is enough.
The flame is a GitHub Octicon (MIT licence).

Usage:  python3 -I streak.py OUTPUT_DIR          fetch and render
        python3 -I streak.py --json              fetch and print the numbers
        python3 -I streak.py OUTPUT_DIR --from STREAK.json   render saved numbers

Environment:
  GITHUB_TOKEN      required for fetching; the workflow's own token is enough
  STREAK_USER       GitHub login (required for fetching)
  STREAK_API_URL    default https://api.github.com/graphql
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Sans-Serif"

THEMES = {
    "dark": {"bg": "#0D1117", "num": "#FEFEFE", "date": "#9E9E9E", "fire": "#FB8C00", "rule": "#E4E2E2"},
    "light": {"bg": "#FFFEFE", "num": "#151515", "date": "#464646", "fire": "#FB8C00", "rule": "#E4E2E2"},
}

# Octicons flame-16, outer outline only, so it fills as a solid flame
FLAME = ("M9.533.753V.752c.217 2.385 1.463 3.626 2.653 4.81C13.37 6.74 14.498 7.863 14.498 10c0 3.5-3 6-6.5 6S1.5 "
         "13.512 1.5 10c0-1.298.536-2.56 1.425-3.286.376-.308.862 0 1.035.454C4.46 8.487 5.581 8.419 6 8c.282-.282.341"
         "-.811-.003-1.5C4.34 3.187 7.035.75 8.77.146c.39-.137.726.194.763.607Z")

YEARS_QUERY = """query($login:String!){user(login:$login){
contributionsCollection{contributionYears}}}"""

YEAR_QUERY = """query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login){
contributionsCollection(from:$from,to:$to){
contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}"""


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- data

def graphql(api: str, token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(api, data=body, method="POST", headers={
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "contrib-streak/1.0",
    })
    last_err = "no response"
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp) or {}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                fail("GitHub rejected the token (HTTP 401)")
            last_err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last_err = type(e).__name__
        else:
            if payload.get("errors"):
                fail("GraphQL error: " + "; ".join(str(e.get("message")) for e in payload["errors"])[:300])
            user = (payload.get("data") or {}).get("user")
            if not user:
                fail(f"no such user {variables.get('login')!r}")
            return user
        time.sleep(5 * (i + 1))
    fail(f"could not reach GitHub ({last_err})")
    return {}


def fetch(api: str, token: str, login: str) -> dict:
    now = datetime.now(timezone.utc)
    years = graphql(api, token, YEARS_QUERY, {"login": login})["contributionsCollection"]["contributionYears"]
    days: dict[str, int] = {}
    for year in sorted(years):
        end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc), now)
        c = graphql(api, token, YEAR_QUERY, {
            "login": login, "from": f"{year}-01-01T00:00:00Z", "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })["contributionsCollection"]
        for week in c["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = int(day["contributionCount"])
    return summarise(days, now.date())


def summarise(days: dict[str, int], today: date) -> dict:
    series = sorted((date.fromisoformat(d), n) for d, n in days.items() if date.fromisoformat(d) <= today)
    active = [d for d, n in series if n > 0]

    longest = {"days": 0, "start": None, "end": None}
    run_start = prev = None
    for d in active:
        if prev is None or d - prev != timedelta(days=1):
            run_start = d
        if (d - run_start).days + 1 > longest["days"]:
            longest = {"days": (d - run_start).days + 1, "start": run_start.isoformat(), "end": d.isoformat()}
        prev = d

    # weeks run Sunday to Saturday, as on the contribution calendar; a week streak
    # is still alive if this week or last week had a contribution
    def week_of(d: date) -> date:
        return d - timedelta(days=(d.weekday() + 1) % 7)

    week_totals: dict[date, int] = {}
    for d, n in series:
        week_totals[week_of(d)] = week_totals.get(week_of(d), 0) + n
    weeks = {"weeks": 0, "start": None, "end": None}
    cursor = week_of(today) if week_totals.get(week_of(today)) else week_of(today) - timedelta(weeks=1)
    while week_totals.get(cursor):
        weeks["weeks"] += 1
        cursor -= timedelta(weeks=1)
    if weeks["weeks"]:
        in_streak = [d for d in active if d > cursor + timedelta(days=6)]
        weeks["start"], weeks["end"] = in_streak[0].isoformat(), in_streak[-1].isoformat()

    return {
        "today": today.isoformat(),
        "total": sum(n for _, n in series),
        "since": active[0].isoformat() if active else None,
        "weeks": weeks,
        "longest": longest,
    }


# ---------------------------------------------------------------- render

def fmt_day(d: date, today: date) -> str:
    return f"{d:%b} {d.day}" + ("" if d.year == today.year else f", {d.year}")


def fmt_span(st: dict, today: date) -> str:
    a, b = date.fromisoformat(st["start"]), date.fromisoformat(st["end"])
    if a == b:
        return fmt_day(a, today)
    return f"{fmt_day(a, today)} - {fmt_day(b, today)}"


def render(s: dict, t: dict) -> str:
    today = date.fromisoformat(s["today"])
    since = date.fromisoformat(s["since"]) if s["since"] else today
    wk, best = s["weeks"], s["longest"]
    wk_dates = fmt_span(wk, today) if wk["weeks"] else fmt_day(today, today)
    best_dates = fmt_span(best, today) if best["days"] else fmt_day(today, today)

    def side(x: float, value: str, label: str, dates: str, delay: int) -> str:
        return (f'<g class="fade" style="animation-delay:{delay}ms">'
                f'<text x="{x}" y="79" text-anchor="middle" font-size="28" font-weight="700" fill="{t["num"]}">'
                f"{escape(value)}</text>"
                f'<text x="{x}" y="115" text-anchor="middle" font-size="14" fill="{t["num"]}">{escape(label)}</text>'
                f'<text x="{x}" y="145" text-anchor="middle" font-size="12" fill="{t["date"]}">{escape(dates)}</text>'
                "</g>")

    label = (f"{s['total']:,} contributions since {since:%b %Y}; week streak {wk['weeks']} weeks; "
             f"longest streak {best['days']} days")
    return "".join([
        '<svg xmlns="http://www.w3.org/2000/svg" width="495" height="195" viewBox="0 0 495 195" '
        f'fill="none" role="img" aria-labelledby="t"><title id="t">{escape(label)}</title>',
        f"<style>text{{font-family:{FONT}}}"
        ".fade{opacity:0;animation:fade .6s ease-in-out forwards}"
        "@keyframes fade{from{opacity:0}to{opacity:1}}"
        "@media (prefers-reduced-motion:reduce){.fade{animation:none;opacity:1}}</style>",
        f'<rect x=".5" y=".5" width="494" height="194" rx="4.5" fill="{t["bg"]}"/>',
        f'<line x1="165" y1="28" x2="165" y2="170" stroke="{t["rule"]}"/>',
        f'<line x1="330" y1="28" x2="330" y2="170" stroke="{t["rule"]}"/>',
        side(82.5, f"{s['total']:,}", "Total Contributions", f"{since:%b} {since.day}, {since.year} - Present", 600),
        side(412.5, f"{best['days']:,}", "Longest Streak", best_dates, 1200),
        # week streak: a ring with the flame sitting in a gap at the top
        '<mask id="gap"><rect width="495" height="195" fill="#fff"/><circle cx="247.5" cy="32" r="13" fill="#000"/></mask>',
        f'<circle cx="247.5" cy="71" r="40" stroke="{t["fire"]}" stroke-width="5" mask="url(#gap)"/>',
        f'<g class="fade" style="animation-delay:400ms">'
        f'<path transform="translate(237.5 20) scale(1.25)" fill="{t["fire"]}" d="{FLAME}"/></g>',
        '<g class="fade" style="animation-delay:900ms">'
        f'<text x="247.5" y="81" text-anchor="middle" font-size="28" font-weight="700" fill="{t["num"]}">'
        f"{wk['weeks']:,}</text>"
        f'<text x="247.5" y="140" text-anchor="middle" font-size="14" font-weight="700" fill="{t["fire"]}">'
        "Week Streak</text>"
        f'<text x="247.5" y="166" text-anchor="middle" font-size="12" fill="{t["date"]}">{escape(wk_dates)}</text>'
        "</g>",
        "</svg>\n",
    ])


def main() -> None:
    args = sys.argv[1:]
    as_json = "--json" in args
    source = None
    if "--from" in args:
        i = args.index("--from")
        if i + 1 >= len(args):
            fail("--from needs a file")
        source = Path(args[i + 1])
        del args[i:i + 2]
    args = [x for x in args if x != "--json"]

    if source:
        stats = json.loads(source.read_text(encoding="utf-8"))
    else:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        login = os.environ.get("STREAK_USER", "").strip()
        if not token:
            fail("GITHUB_TOKEN is not set")
        if not login or not all(ch.isalnum() or ch == "-" for ch in login) or len(login) > 39:
            fail("STREAK_USER must be a GitHub login")
        api = os.environ.get("STREAK_API_URL", "https://api.github.com/graphql").strip()
        if not api.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            fail("STREAK_API_URL must be an https URL")
        stats = fetch(api, token, login)

    if as_json:
        print(json.dumps(stats, indent=1))
        return

    out_dir = Path(args[0] if args else ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        target = out_dir / f"streak-{name}.svg"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(render(stats, theme), encoding="utf-8")
        tmp.replace(target)

    print(f"{stats['total']:,} contributions, week streak {stats['weeks']['weeks']}, "
          f"longest {stats['longest']['days']} days")


if __name__ == "__main__":
    main()
