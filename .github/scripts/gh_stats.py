#!/usr/bin/env python3
"""Render GitHub profile stats as dark/light terminal-style SVG cards.

Standard library only: no pip installs, no third-party code at runtime.
Private repositories and contributions count when the token can see them.

Usage:  python3 -I gh_stats.py OUTPUT_DIR          fetch and render
        python3 -I gh_stats.py --json              fetch and print the numbers (no repo names)
        python3 -I gh_stats.py OUTPUT_DIR --from STATS.json   render saved numbers

Environment:
  GITHUB_TOKEN      required for fetching; a token that can read your private
                    repositories' metadata makes private data count
  STATS_USER        GitHub login (required for fetching)
  STATS_NAME        name shown in the window title (default: the login)
  STATS_TOP         number of language rows to show (default 6)
  STATS_IGNORE      comma-separated language names to drop, case-insensitive
  STATS_TZ          IANA time zone for the "updated" date (default UTC)
  STATS_API_URL     default https://api.github.com/graphql
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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# same look as waka_card.py, so the two cards sit together
THEMES = {
    "dark": {
        "bg": "#0D1117", "border": "#30363D", "bar": "#161B22",
        "text": "#E6EDF3", "muted": "#8B949E", "gutter": "#484F58", "rule": "#21262D",
        "track": "#21262D", "prompt": "#7EE787", "flag": "#79C0FF", "num": "#FFA657",
        "mode_bg": "#2F81F7", "mode_fg": "#0D1117", "seg_bg": "#21262D",
        "other": "#8B949E",
    },
    "light": {
        "bg": "#F6F8FA", "border": "#D0D7DE", "bar": "#EAEEF2",
        "text": "#1F2328", "muted": "#656D76", "gutter": "#8C959F", "rule": "#D8DEE4",
        "track": "#E1E6EB", "prompt": "#116329", "flag": "#0550AE", "num": "#953800",
        "mode_bg": "#0969DA", "mode_fg": "#FFFFFF", "seg_bg": "#D8DEE4",
        "other": "#6E7781",
    },
}

MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"

# layout (px)
W = 720
TITLE_H = 34
STATUS_H = 26
LINE_H = 24
FIRST_BASE = TITLE_H + 28
GUTTER_X = 44          # line numbers, right-aligned
RULE_X = 56
CODE_X = 68
VALUE_X = 186
CELLS_X = 186
CELLS = 36
CELL_W, CELL_GAP, CELL_H = 9, 3, 11
SHARE_X = W - 24
NAME_MAX = 13

YEARS_QUERY = """query($login:String!){user(login:$login){
contributionsCollection{contributionYears}}}"""

YEAR_QUERY = """query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login){
contributionsCollection(from:$from,to:$to){
totalCommitContributions totalIssueContributions totalPullRequestContributions
totalPullRequestReviewContributions restrictedContributionsCount
contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}"""

REPOS_QUERY = """query($login:String!,$after:String){user(login:$login){
repositories(first:100,after:$after,ownerAffiliations:OWNER,isFork:false){
pageInfo{hasNextPage endCursor}
nodes{isPrivate stargazerCount
languages(first:25,orderBy:{field:SIZE,direction:DESC}){edges{size node{name color}}}}}}}"""


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- data

def graphql(api: str, token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(api, data=body, method="POST", headers={
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "gh-stats/1.0",
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
    totals = {"commits": 0, "issues": 0, "prs": 0, "reviews": 0, "restricted": 0}
    for year in sorted(years):
        end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc), now)
        c = graphql(api, token, YEAR_QUERY, {
            "login": login, "from": f"{year}-01-01T00:00:00Z", "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })["contributionsCollection"]
        totals["commits"] += c["totalCommitContributions"]
        totals["issues"] += c["totalIssueContributions"]
        totals["prs"] += c["totalPullRequestContributions"]
        totals["reviews"] += c["totalPullRequestReviewContributions"]
        totals["restricted"] += c["restrictedContributionsCount"]
        for week in c["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = int(day["contributionCount"])

    repos = {"public": 0, "private": 0, "stars": 0}
    langs: dict[str, list] = {}
    after = None
    while True:
        page = graphql(api, token, REPOS_QUERY, {"login": login, "after": after})["repositories"]
        for repo in page["nodes"]:
            repos["private" if repo["isPrivate"] else "public"] += 1
            repos["stars"] += repo["stargazerCount"]
            for edge in repo["languages"]["edges"]:
                name, color = edge["node"]["name"], edge["node"]["color"]
                entry = langs.setdefault(name, [0, color])
                entry[0] += edge["size"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    return summarise(days, totals, repos, langs, now.date())


def summarise(days: dict[str, int], totals: dict, repos: dict, langs: dict, today: date) -> dict:
    """Reduce the raw data to the numbers the card shows (no repository names)."""
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

    # a streak is still alive if the last contribution was today or yesterday
    current = {"days": 0, "start": None, "end": None}
    last_day = series[-1][0] if series else today
    count = dict(series)
    cursor = last_day if count.get(last_day) else last_day - timedelta(days=1)
    end = cursor
    while count.get(cursor):
        current = {"days": (end - cursor).days + 1, "start": cursor.isoformat(), "end": end.isoformat()}
        cursor -= timedelta(days=1)

    year_ago = last_day - timedelta(days=365)
    return {
        "total": sum(n for _, n in series),
        "since": active[0].isoformat() if active else None,
        "last_year": sum(n for d, n in series if d > year_ago),
        "current": current,
        "longest": longest,
        **totals,
        **repos,
        "languages": sorted(([n, s, c] for n, (s, c) in langs.items()), key=lambda x: x[1], reverse=True),
    }


# ---------------------------------------------------------------- render

def num(n: int) -> str:
    return f"{n:,}"


def plural(n: int, word: str) -> str:
    return f"{num(n)} {word}" + ("" if n == 1 else "s")


def span(s: dict) -> str:
    a, b = date.fromisoformat(s["start"]), date.fromisoformat(s["end"])
    if a.year == b.year:
        return f"{a.day} {a:%b} – {b.day} {b:%b %Y}"
    return f"{a.day} {a:%b %Y} – {b.day} {b:%b %Y}"


def clip(name: str) -> str:
    return name if len(name) <= NAME_MAX else name[: NAME_MAX - 1] + "…"


def render(stats: dict, theme: dict, top_n: int, ignore: set[str], updated: str, user: str) -> str:
    t = theme
    langs = [(n, s, c) for n, s, c in stats["languages"] if n.lower() not in ignore and s > 0]
    total_bytes = sum(s for _, s, _ in langs)
    entries = [(n, s, c or t["other"]) for n, s, c in langs[:top_n]]
    other = sum(s for _, s, _ in langs[top_n:])
    if other > 0:
        entries.append(("Other", other, t["other"]))
    private_seen = stats["private"] > 0  # only a token that can see private repos finds any

    # each info line: label, then (text, is_number) pieces
    sep = (" · ", False)
    info: list[tuple[str, list[tuple[str, bool]]]] = []
    if stats["since"]:
        since = date.fromisoformat(stats["since"])
        info.append(("contributions", [(num(stats["total"]), True), (f" since {since:%b %Y}", False), sep,
                                       (num(stats["last_year"]), True), (" last year", False)]))
    streak = [(num(stats["current"]["days"]), True), (" days now", False)]
    if stats["longest"]["days"]:
        streak += [sep, ("longest ", False), (num(stats["longest"]["days"]), True),
                   (f" days ({span(stats['longest'])})", False)]
    info.append(("streak", streak))
    activity = []
    for key, word in (("commits", "commit"), ("prs", "PR"), ("issues", "issue"), ("reviews", "review")):
        n, w = plural(stats[key], word).split(" ", 1)
        activity += [(n, True), (f" {w}", False), sep]
    if stats["restricted"]:
        activity += [(num(stats["restricted"]), True), (" private", False), sep]
    info.append(("activity", activity[:-1]))
    repos = stats["public"] + stats["private"]
    info.append(("repos", [(num(repos), True), (" owned (", False), (num(stats["public"]), True),
                           (" public · ", False), (num(stats["private"]), True), (" private)", False), sep,
                           (num(stats["stars"]), True), (" stars" if stats["stars"] != 1 else " star", False)]))

    # code lines: command, info, blank, [header + rows | comment], blank, prompt
    body_lines = 1 + len(info) + 1 + (1 + len(entries) if entries else 1) + 2
    H = FIRST_BASE + (body_lines - 1) * LINE_H + 16 + STATUS_H

    out: list[str] = []
    a = out.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
      f'role="img" aria-labelledby="t">')
    a(f'<title id="t">GitHub stats: {escape(num(stats["total"]))} contributions, '
      f'{escape(num(stats["commits"]))} commits, {escape(num(repos))} repositories</title>')
    a("<style>")
    a(f"text{{font-family:{MONO};font-size:13px}}")
    a(".s{font-size:11px}.b{font-weight:700}")
    a(".px{animation:px .3s ease-out both}.fade{animation:fade .5s ease-out both}")
    a(".cur{animation:blink 1.1s steps(1) infinite}")
    a("@keyframes px{from{opacity:0}to{opacity:1}}")
    a("@keyframes fade{from{opacity:0;transform:translateX(-4px)}to{opacity:1;transform:none}}")
    a("@keyframes blink{50%{opacity:0}}")
    a("@media (prefers-reduced-motion:reduce){.px,.fade,.cur{animation:none}}")
    a("</style>")
    a(f'<clipPath id="card"><rect width="{W}" height="{H}" rx="10"/></clipPath>')
    a('<g clip-path="url(#card)">')
    a(f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>')

    # title bar
    a(f'<rect width="{W}" height="{TITLE_H}" fill="{t["bar"]}"/>')
    a(f'<line x1="0" y1="{TITLE_H - .5}" x2="{W}" y2="{TITLE_H - .5}" stroke="{t["border"]}"/>')
    for i, c in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        a(f'<circle cx="{20 + i * 18}" cy="{TITLE_H / 2}" r="5.5" fill="{c}"/>')
    title = f"{user}@github: ~/stats" if user else "~/stats"
    a(f'<text x="{W / 2}" y="{TITLE_H / 2 + 4}" text-anchor="middle" class="s" '
      f'fill="{t["muted"]}">{escape(title)}</text>')

    # gutter
    a(f'<line x1="{RULE_X}" y1="{TITLE_H}" x2="{RULE_X}" y2="{H - STATUS_H}" stroke="{t["rule"]}"/>')
    for n in range(body_lines):
        a(f'<text x="{GUTTER_X}" y="{FIRST_BASE + n * LINE_H}" text-anchor="end" class="s" '
          f'fill="{t["gutter"]}">{n + 1}</text>')

    def line_y(n: int) -> int:
        return FIRST_BASE + n * LINE_H

    # 1: command
    scope = "--include-private" if private_seen else "--public-only"
    a(f'<text x="{CODE_X}" y="{line_y(0)}" fill="{t["text"]}">'
      f'<tspan fill="{t["prompt"]}" class="b">$</tspan> gh stats '
      f'<tspan fill="{t["flag"]}">{scope}</tspan></text>')

    # key/value lines, numbers highlighted
    n = 1
    for label, pieces in info:
        y = line_y(n)
        a(f'<g class="fade" style="animation-delay:{120 + (n - 1) * 80}ms">')
        a(f'<text x="{CODE_X}" y="{y}" fill="{t["muted"]}">{escape(label)}</text>')
        spans = "".join(f'<tspan fill="{t["num"] if is_num else t["muted"]}">{escape(s)}</tspan>'
                        for s, is_num in pieces)
        a(f'<text x="{VALUE_X}" y="{y}" xml:space="preserve">{spans}</text>')
        a("</g>")
        n += 1
    n += 1

    if not entries:
        a(f'<text x="{CODE_X}" y="{line_y(n)}" fill="{t["muted"]}"># no language data</text>')
        n += 1
    else:
        y = line_y(n)
        for x, label, anchor in ((CODE_X, "LANGUAGE", "start"), (CELLS_X, "USAGE", "start"),
                                 (SHARE_X, "SHARE", "end")):
            a(f'<text x="{x}" y="{y}" text-anchor="{anchor}" class="s" letter-spacing="1" '
              f'fill="{t["muted"]}">{label}</text>')
        n += 1

        max_bytes = max(s for _, s, _ in entries)
        for i, (name, size, color) in enumerate(entries):
            y = line_y(n)
            base = 500 + i * 90
            a(f'<g class="fade" style="animation-delay:{base}ms">')
            a(f'<rect x="{CODE_X}" y="{y - 9}" width="8" height="8" rx="2" fill="{color}"/>')
            a(f'<text x="{CODE_X + 16}" y="{y}" fill="{t["text"]}">{escape(clip(name))}</text>')
            a(f'<text x="{SHARE_X}" y="{y}" text-anchor="end" fill="{t["text"]}">'
              f'{100 * size / total_bytes:.1f}%</text>')
            a("</g>")
            level = CELLS * size / max_bytes
            cy = y - CELL_H + 1
            for j in range(CELLS):
                cx = CELLS_X + j * (CELL_W + CELL_GAP)
                fill = min(max(level - j, 0.0), 1.0)
                if j == 0:
                    fill = max(fill, 1.0)  # every language gets at least one cell
                a(f'<rect x="{cx}" y="{cy}" width="{CELL_W}" height="{CELL_H}" rx="1.5" '
                  f'fill="{t["track"]}"/>')
                if fill > 0:
                    a(f'<rect class="px" style="animation-delay:{base + 120 + j * 15}ms" x="{cx}" '
                      f'y="{cy}" width="{CELL_W}" height="{CELL_H}" rx="1.5" fill="{color}" '
                      f'fill-opacity="{max(fill, 0.3):.2f}"/>')
            n += 1

    # blank line, then a prompt with a blinking cursor
    n += 1
    y = line_y(n)
    a(f'<text x="{CODE_X}" y="{y}" fill="{t["prompt"]}" class="b">$</text>')
    a(f'<rect class="cur" x="{CODE_X + 15}" y="{y - 12}" width="8" height="15" fill="{t["text"]}"/>')

    # status line (vim/lualine style)
    sy = H - STATUS_H
    a(f'<rect y="{sy}" width="{W}" height="{STATUS_H}" fill="{t["bar"]}"/>')
    a(f'<line x1="0" y1="{sy + .5}" x2="{W}" y2="{sy + .5}" stroke="{t["border"]}"/>')
    a(f'<rect y="{sy}" width="78" height="{STATUS_H}" fill="{t["mode_bg"]}"/>')
    a(f'<text x="39" y="{sy + 17}" text-anchor="middle" class="s b" fill="{t["mode_fg"]}">NORMAL</text>')
    a(f'<rect x="78" y="{sy}" width="92" height="{STATUS_H}" fill="{t["seg_bg"]}"/>')
    a(f'<text x="124" y="{sy + 17}" text-anchor="middle" class="s" fill="{t["text"]}">github</text>')
    reach = "public + private" if private_seen else "public only"
    a(f'<text x="{W - 14}" y="{sy + 17}" text-anchor="end" class="s" fill="{t["muted"]}">'
      f'{reach} · updated {escape(updated)}</text>')

    a("</g>")
    a(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="10" fill="none" stroke="{t["border"]}"/>')
    a("</svg>")
    return "\n".join(out) + "\n"


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
        login = os.environ.get("STATS_USER", "").strip()
        if not token:
            fail("GITHUB_TOKEN is not set")
        if not login or not all(ch.isalnum() or ch == "-" for ch in login) or len(login) > 39:
            fail("STATS_USER must be a GitHub login")
        api = os.environ.get("STATS_API_URL", "https://api.github.com/graphql").strip()
        if not api.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            fail("STATS_API_URL must be an https URL")
        stats = fetch(api, token, login)

    if as_json:
        print(json.dumps(stats, indent=1))
        return

    try:
        top_n = max(1, min(int(os.environ.get("STATS_TOP", "6")), 8))
    except ValueError:
        fail("STATS_TOP must be a number")
    ignore = {s.strip().lower() for s in os.environ.get("STATS_IGNORE", "").split(",") if s.strip()}
    try:
        tz = ZoneInfo(os.environ.get("STATS_TZ", "UTC").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    updated = datetime.now(tz).strftime("%d %b %Y")
    name = os.environ.get("STATS_NAME", "") or os.environ.get("STATS_USER", "")
    user = "".join(ch for ch in name if ch.isalnum() or ch in "_-.")[:39]

    out_dir = Path(args[0] if args else ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    for theme_name, theme in THEMES.items():
        target = out_dir / f"stats-{theme_name}.svg"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(render(stats, theme, top_n, ignore, updated, user), encoding="utf-8")
        tmp.replace(target)

    print(f"rendered: {num(stats['total'])} contributions, {num(stats['public'] + stats['private'])} repos, "
          f"{len(stats['languages'])} languages")


if __name__ == "__main__":
    main()
