#!/usr/bin/env python3
"""Render WakaTime coding stats as dark/light terminal-style SVG cards.

Standard library only: no pip installs, no third-party code at runtime.

Usage:  python3 -I waka_card.py OUTPUT_DIR

Environment:
  WAKATIME_API_KEY  required
  WAKA_RANGE        last_7_days (default) | last_30_days | last_6_months | last_year | all_time
  WAKA_TOP          number of language rows to show (default 5)
  WAKA_IGNORE       comma-separated language names to drop, case-insensitive (e.g. "GDScript, GDScript3")
  WAKA_API_BASE     default https://wakatime.com/api/v1 (set for Wakapi etc.)
  WAKA_TZ           IANA time zone for the "updated" date (default UTC)
  WAKA_USER         name shown in the window title, e.g. CN_CoreSteb (optional)
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from xml.sax.saxutils import escape

RANGES = {
    "last_7_days": "Last 7 days",
    "last_30_days": "Last 30 days",
    "last_6_months": "Last 6 months",
    "last_year": "Last year",
    "all_time": "All time",
}

RANGE_FLAGS = {
    "last_7_days": "7d",
    "last_30_days": "30d",
    "last_6_months": "6mo",
    "last_year": "1y",
    "all_time": "all",
}

# GitHub-style syntax colours, so the card reads like highlighted code
THEMES = {
    "dark": {
        "bg": "#0D1117", "border": "#30363D", "bar": "#161B22",
        "text": "#E6EDF3", "muted": "#8B949E", "gutter": "#484F58", "rule": "#21262D",
        "track": "#21262D", "prompt": "#7EE787", "flag": "#79C0FF", "num": "#FFA657",
        "mode_bg": "#2F81F7", "mode_fg": "#0D1117", "seg_bg": "#21262D",
        "other": "#8B949E",
        "palette": ["#79C0FF", "#D2A8FF", "#7EE787", "#FFA657",
                    "#FF7B72", "#A5D6FF", "#E3B341", "#56D4DD"],
    },
    "light": {
        "bg": "#F6F8FA", "border": "#D0D7DE", "bar": "#EAEEF2",
        "text": "#1F2328", "muted": "#656D76", "gutter": "#8C959F", "rule": "#D8DEE4",
        "track": "#E1E6EB", "prompt": "#116329", "flag": "#0550AE", "num": "#953800",
        "mode_bg": "#0969DA", "mode_fg": "#FFFFFF", "seg_bg": "#D8DEE4",
        "other": "#6E7781",
        "palette": ["#0550AE", "#8250DF", "#116329", "#953800",
                    "#CF222E", "#0A3069", "#7D4E00", "#1B7C83"],
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
TIME_X = 186
CELLS_X = 262
CELLS = 30
CELL_W, CELL_GAP, CELL_H = 9, 3, 11
SHARE_X = W - 24
NAME_MAX = 13


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def fetch(api_base: str, rng: str, key: str) -> dict:
    url = f"{api_base}/users/current/stats/{rng}"
    token = base64.b64encode(key.encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "User-Agent": "waka-card/1.0",
    })
    attempts = 5
    last_err = "no response"
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                data = (json.load(resp) or {}).get("data") or {}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                fail(f"WakaTime rejected the API key (HTTP {e.code})")
            last_err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last_err = type(e).__name__
        else:
            ready = status == 200 and data.get("is_up_to_date") is not False
            if ready or (i == attempts - 1 and data.get("languages")):
                return data
            last_err = "stats still being calculated"
        if i < attempts - 1:
            time.sleep(15 * (i + 1))
    fail(f"could not get stats ({last_err})")
    return {}


def fmt_duration(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m:02d}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def clip(name: str) -> str:
    return name if len(name) <= NAME_MAX else name[: NAME_MAX - 1] + "\u2026"


def prepare(data: dict, top_n: int, ignore: set[str]) -> dict:
    langs: list[tuple[str, float]] = []
    other = 0.0
    for item in data.get("languages") or []:
        name = str(item.get("name") or "").strip()[:60]
        try:
            secs = float(item.get("total_seconds") or 0)
        except (TypeError, ValueError):
            continue
        if not name or secs <= 0 or name.lower() in ignore:
            continue
        if name.lower() == "other":
            other += secs
        else:
            langs.append((name, secs))
    langs.sort(key=lambda x: x[1], reverse=True)
    other += sum(s for _, s in langs[top_n:])
    top = langs[:top_n]
    total = sum(s for _, s in top) + other

    days = 0
    for k in ("days_minus_holidays", "days_including_holidays"):
        try:
            days = int(data.get(k) or 0)
        except (TypeError, ValueError):
            days = 0
        if days > 0:
            break
    return {"top": top, "other": other, "total": total, "days": days or 7}


def render(stats: dict, theme: dict, rng: str, updated: str, user: str) -> str:
    t = theme
    top, other, total = stats["top"], stats["other"], stats["total"]
    p = t["palette"]

    entries = [(name, secs, p[i % len(p)]) for i, (name, secs) in enumerate(top)]
    if other > 0:
        entries.append(("Other", other, t["other"]))

    # code lines: command, summary, [header + rows | comment], blank, prompt
    body_lines = 2 + (1 + len(entries) if total > 0 else 1) + 2
    H = FIRST_BASE + (body_lines - 1) * LINE_H + 16 + STATUS_H

    out: list[str] = []
    a = out.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
      f'role="img" aria-labelledby="t">')
    a(f'<title id="t">WakaTime coding activity, {escape(RANGES[rng].lower())}: '
      f'{escape(fmt_duration(total))}</title>')
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
    title = f"{user}@github: ~/wakatime" if user else "~/wakatime"
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
    a(f'<text x="{CODE_X}" y="{line_y(0)}" fill="{t["text"]}">'
      f'<tspan fill="{t["prompt"]}" class="b">$</tspan> wakatime stats '
      f'<tspan fill="{t["flag"]}">--range={RANGE_FLAGS[rng]}</tspan></text>')

    # 2: summary, like `ls -l`\'s "total" line
    a(f'<text x="{CODE_X}" y="{line_y(1)}" fill="{t["muted"]}">total '
      f'<tspan fill="{t["num"]}">{escape(fmt_duration(total))}</tspan> · avg '
      f'<tspan fill="{t["num"]}">{escape(fmt_duration(total / stats["days"]))}</tspan>/day</text>')

    n = 2
    if total <= 0:
        a(f'<text x="{CODE_X}" y="{line_y(n)}" fill="{t["muted"]}">'
          f'# no coding activity tracked in this period</text>')
        n += 1
    else:
        y = line_y(n)
        for x, label, anchor in ((CODE_X, "LANGUAGE", "start"), (TIME_X, "TIME", "start"),
                                 (CELLS_X, "USAGE", "start"), (SHARE_X, "SHARE", "end")):
            a(f'<text x="{x}" y="{y}" text-anchor="{anchor}" class="s" letter-spacing="1" '
              f'fill="{t["muted"]}">{label}</text>')
        n += 1

        max_secs = max(s for _, s, _ in entries)
        for i, (name, secs, color) in enumerate(entries):
            y = line_y(n)
            base = 250 + i * 90
            a(f'<g class="fade" style="animation-delay:{base}ms">')
            a(f'<text x="{CODE_X}" y="{y}" fill="{color}">{escape(clip(name))}</text>')
            a(f'<text x="{TIME_X}" y="{y}" fill="{t["muted"]}">{escape(fmt_duration(secs))}</text>')
            a(f'<text x="{SHARE_X}" y="{y}" text-anchor="end" fill="{t["text"]}">'
              f'{100 * secs / total:.1f}%</text>')
            a("</g>")
            level = CELLS * secs / max_secs
            cy = y - CELL_H + 1
            for j in range(CELLS):
                cx = CELLS_X + j * (CELL_W + CELL_GAP)
                fill = min(max(level - j, 0.0), 1.0)
                if j == 0:
                    fill = max(fill, 1.0)  # every language gets at least one cell
                if fill <= 0:
                    a(f'<rect x="{cx}" y="{cy}" width="{CELL_W}" height="{CELL_H}" rx="1.5" '
                      f'fill="{t["track"]}"/>')
                    continue
                a(f'<rect x="{cx}" y="{cy}" width="{CELL_W}" height="{CELL_H}" rx="1.5" '
                  f'fill="{t["track"]}"/>')
                a(f'<rect class="px" style="animation-delay:{base + 120 + j * 18}ms" x="{cx}" '
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
    a(f'<text x="124" y="{sy + 17}" text-anchor="middle" class="s" fill="{t["text"]}">wakatime</text>')
    a(f'<text x="{W - 14}" y="{sy + 17}" text-anchor="end" class="s" fill="{t["muted"]}">'
      f'utf-8 · updated {escape(updated)}</text>')

    a("</g>")
    a(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="10" fill="none" stroke="{t["border"]}"/>')
    a("</svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    key = os.environ.get("WAKATIME_API_KEY", "").strip()
    if not key:
        fail("WAKATIME_API_KEY is not set")

    rng = os.environ.get("WAKA_RANGE", "last_7_days").strip()
    if rng not in RANGES:
        fail(f"WAKA_RANGE must be one of: {', '.join(RANGES)}")
    try:
        top_n = max(1, min(int(os.environ.get("WAKA_TOP", "5")), 8))
    except ValueError:
        fail("WAKA_TOP must be a number")
    ignore = {s.strip().lower() for s in os.environ.get("WAKA_IGNORE", "").split(",") if s.strip()}
    api_base = os.environ.get("WAKA_API_BASE", "https://wakatime.com/api/v1").strip().rstrip("/")
    if not api_base.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        fail("WAKA_API_BASE must be an https URL")

    stats = prepare(fetch(api_base, rng, key), top_n, ignore)
    try:
        tz = ZoneInfo(os.environ.get("WAKA_TZ", "UTC").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    updated = datetime.now(tz).strftime("%d %b %Y")

    user = "".join(ch for ch in os.environ.get("WAKA_USER", "") if ch.isalnum() or ch in "_-.")[:39]

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        target = out_dir / f"waka-{name}.svg"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(render(stats, theme, rng, updated, user), encoding="utf-8")
        tmp.replace(target)

    print(f"rendered {len(stats['top'])} languages, total {fmt_duration(stats['total'])}")


if __name__ == "__main__":
    main()
