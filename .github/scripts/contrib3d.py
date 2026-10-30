#!/usr/bin/env python3
"""Render the last year's contribution calendar as an isometric 3D chart (SVG).

Standard library only: no pip installs, no third-party code at runtime.
The calendar already counts private contributions when the profile shows
them, so the workflow's own token is enough. The background is transparent,
so a card sits on either GitHub theme.

Usage:  python3 -I contrib3d.py OUTPUT_DIR [--light STYLE] [--dark STYLE]
        python3 -I contrib3d.py --json                      fetch and print the calendar
        python3 -I contrib3d.py OUTPUT_DIR --from CAL.json  render a saved calendar

Writes 3d-light.svg and 3d-dark.svg.
Light styles: green, south-season, gitblock.  Dark styles: night-view, night-green, night-rainbow.

Environment:
  GITHUB_TOKEN      required for fetching; the workflow's own token is enough
  CONTRIB3D_USER    GitHub login (required for fetching)
  CONTRIB3D_API_URL default https://api.github.com/graphql
"""

from __future__ import annotations

import colorsys
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

# geometry (px): ground cell edge, viewBox, origin of week 0 / weekday 0
CELL = 17
A, B = CELL * math.cos(math.pi / 6), CELL / 2
VB_W, VB_H = 960, 680
X0, Y0 = 143, 150
MAX_H = 110
GAP = 0.07
BRICK = 7  # gitblock: one brick's height

QUERY = """query($login:String!){user(login:$login){contributionsCollection{contributionCalendar{
totalContributions weeks{contributionDays{date weekday contributionCount contributionLevel}}}}}}"""

TEXT = {
    "light": {"text": "#24292F", "muted": "#57606A"},
    "dark": {"text": "#E6EDF3", "muted": "#8B949E"},
}

SEASON_SOUTH = {  # month -> season, southern hemisphere
    12: "summer", 1: "summer", 2: "summer", 3: "autumn", 4: "autumn", 5: "autumn",
    6: "winter", 7: "winter", 8: "winter", 9: "spring", 10: "spring", 11: "spring",
}
SEASONS = {
    "summer": ["#9BE9A8", "#40C463", "#30A14E", "#216E39"],
    "autumn": ["#FDE68A", "#FBBF24", "#F59E0B", "#D97706"],
    "winter": ["#D4D4D8", "#A1A1AA", "#71717A", "#52525B"],
    "spring": ["#FBCFE8", "#F9A8D4", "#EC4899", "#BE185D"],
}


def style_colour(style: str, level: int, week: int, day: date) -> str:
    if style == "green":
        return ["#EBEDF0", "#9BE9A8", "#40C463", "#30A14E", "#216E39"][level]
    if style == "south-season":
        return "#EBEDF0" if level == 0 else SEASONS[SEASON_SOUTH[day.month]][level - 1]
    if style == "gitblock":
        return ["#3CB043", "#F2F2F2", "#1E5AA8", "#F2CD37", "#C91A09"][level]
    if style == "night-view":
        return ["#1A2130", "#123E7C", "#1D63C7", "#2F8CF0", "#5EC8FF"][level]
    if style == "night-green":
        return ["#1A2130", "#0E4429", "#006D32", "#26A641", "#39D353"][level]
    if style == "night-rainbow":
        if level == 0:
            return "#1A2130"
        r, g, b = colorsys.hls_to_rgb((week / 53 + 0.55) % 1, 0.22 + 0.1 * level, 0.75)
        return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"
    raise ValueError(style)


LIGHT_STYLES = ("green", "south-season", "gitblock")
DARK_STYLES = ("night-view", "night-green", "night-rainbow")


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- data

def fetch(api: str, token: str, login: str) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(api, data=body, method="POST", headers={
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "contrib-3d/1.0",
    })
    payload: dict = {}
    last_err = "no response"
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp) or {}
            break
        except urllib.error.HTTPError as e:
            if e.code == 401:
                fail("GitHub rejected the token (HTTP 401)")
            last_err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last_err = type(e).__name__
        time.sleep(5 * (i + 1))
    else:
        fail(f"could not reach GitHub ({last_err})")

    if payload.get("errors"):
        fail("GraphQL error: " + "; ".join(str(e.get("message")) for e in payload["errors"])[:300])
    try:
        cal = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    except (KeyError, TypeError):
        fail(f"no contribution calendar for user {login!r}")
    days = [[d["date"], int(d["weekday"]), int(d["contributionCount"]), LEVELS.get(d["contributionLevel"], 0)]
            for w in cal["weeks"] for d in w["contributionDays"]]
    if not days:
        fail("empty contribution calendar")
    return {"total": int(cal["totalContributions"]), "days": days}


# ---------------------------------------------------------------- render

def proj(gx: float, gy: float, z: float = 0) -> tuple[float, float]:
    return X0 + (gx - gy) * A, Y0 + (gx + gy) * B - z


def pts(*ps: tuple[float, float]) -> str:
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in ps) + "Z"


def shade(color: str, k: float) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{max(0, min(255, round(c * k))):02X}" for c in (r, g, b))


def prism(gx: int, gy: int, h: float, c: str, bricks: bool) -> str:
    x0, x1, y0, y1 = gx + GAP, gx + 1 - GAP, gy + GAP, gy + 1 - GAP
    n, e, s, w = proj(x0, y0, h), proj(x1, y0, h), proj(x1, y1, h), proj(x0, y1, h)
    s0, e0, w0 = proj(x1, y1), proj(x1, y0), proj(x0, y1)
    out = [
        f'<path d="{pts(w0, s0, s, w)}" fill="{shade(c, .82)}"/>',  # faces +weekday (down-left)
        f'<path d="{pts(s0, e0, e, s)}" fill="{shade(c, .66)}"/>',  # faces +week (down-right)
        f'<path d="{pts(n, e, s, w)}" fill="{c}"/>',
    ]
    if bricks:
        seam = shade(c, .55)
        for k in range(1, round(h / BRICK)):
            z = k * BRICK
            out.append(f'<path d="M{proj(x0, y1, z)[0]:.1f} {proj(x0, y1, z)[1]:.1f} '
                       f'L{proj(x1, y1, z)[0]:.1f} {proj(x1, y1, z)[1]:.1f} '
                       f'L{proj(x1, y0, z)[0]:.1f} {proj(x1, y0, z)[1]:.1f}" '
                       f'stroke="{seam}" stroke-width=".6" fill="none"/>')
        cx, cy = proj(gx + .5, gy + .5, h)
        rx, ry, sh = A * .28 * math.sqrt(2), B * .28 * math.sqrt(2), 3
        out.append(f'<path d="M{cx - rx:.1f} {cy:.1f} v{-sh} A{rx:.1f} {ry:.1f} 0 0 1 {cx + rx:.1f} {cy - sh:.1f} '
                   f'v{sh} A{rx:.1f} {ry:.1f} 0 0 1 {cx - rx:.1f} {cy:.1f}Z" fill="{shade(c, .75)}"/>')
        out.append(f'<ellipse cx="{cx:.1f}" cy="{cy - sh:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{shade(c, 1.06)}"/>')
    return "".join(out)


def render(cal: dict, style: str, theme: str) -> str:
    t = TEXT[theme]
    bricks = style == "gitblock"
    days = [(date.fromisoformat(d), wd, n, lvl) for d, wd, n, lvl in cal["days"]]
    first = days[0][0]
    peak = max((n for _, _, n, _ in days), default=0) or 1

    cells = []
    for d, wd, n, lvl in days:
        week = ((d - first).days + days[0][1]) // 7
        if lvl == 0 or n == 0:
            h = 3 if bricks else 1.5
        else:
            h = 4 + (MAX_H - 4) * math.sqrt(n / peak)
            if bricks:
                h = max(1, round(h / BRICK)) * BRICK
        cells.append((week, wd, h, style_colour(style, lvl, week, d), d, n))
    cells.sort(key=lambda c: (c[0] + c[1], c[0]))

    weeks = max(c[0] for c in cells) + 1
    body = []
    for week, wd, h, colour, _, _ in cells:
        ox, oy = proj(week + .5, wd + .5)
        body.append(f'<g class="b w{week}" style="transform-origin:{ox:.1f}px {oy:.1f}px">'
                    f"{prism(week, wd, h, colour, bricks)}</g>")

    best = max(days, key=lambda x: x[2])
    span = f"{first.day} {first:%b %Y} – {days[-1][0].day} {days[-1][0]:%b %Y}"
    accent = style_colour(style, 4, weeks // 2, best[0])
    total = cal["total"]
    stats = [
        f'<text x="40" y="560" font-size="44" font-weight="700" fill="{t["text"]}">{total:,}</text>',
        f'<text x="40" y="590" font-size="16" fill="{t["muted"]}">contributions in the last year</text>',
        f'<rect x="40" y="606" width="36" height="4" rx="2" fill="{accent}"/>',
        f'<text x="40" y="636" font-size="14" fill="{t["muted"]}">busiest day: '
        f'<tspan fill="{t["text"]}" font-weight="600">{best[2]:,}</tspan> on {best[0].day} {best[0]:%b %Y}</text>',
        f'<text x="{VB_W - 40}" y="56" font-size="14" text-anchor="end" fill="{t["muted"]}">{escape(span)}</text>',
    ]

    css = (f"text{{font-family:{FONT}}}"
           ".b{transform-box:view-box;animation:grow .7s cubic-bezier(.2,.8,.3,1) both}"
           "@keyframes grow{from{transform:scaleY(0)}}"
           + "".join(f".w{i}{{animation-delay:{i * 14}ms}}" for i in range(weeks))
           + ".s{animation:fade .8s ease-out .4s both}@keyframes fade{from{opacity:0}}"
           "@media (prefers-reduced-motion:reduce){.b,.s{animation:none}}")
    label = f"3D contribution calendar: {total:,} contributions, {span}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{VB_W}" height="{VB_H}" '
            f'viewBox="0 0 {VB_W} {VB_H}" role="img" aria-labelledby="t">'
            f'<title id="t">{escape(label)}</title><style>{css}</style>'
            + "".join(body) + '<g class="s">' + "".join(stats) + "</g></svg>\n")


def main() -> None:
    args = sys.argv[1:]

    def option(flag: str, default: str | None = None) -> str | None:
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                fail(f"{flag} needs a value")
            value = args[i + 1]
            del args[i:i + 2]
            return value
        return default

    source = option("--from")
    light = option("--light", "green")
    dark = option("--dark", "night-view")
    as_json = "--json" in args
    args = [x for x in args if x != "--json"]
    if light not in LIGHT_STYLES:
        fail(f"--light must be one of: {', '.join(LIGHT_STYLES)}")
    if dark not in DARK_STYLES:
        fail(f"--dark must be one of: {', '.join(DARK_STYLES)}")

    if source:
        cal = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        login = os.environ.get("CONTRIB3D_USER", "").strip()
        if not token:
            fail("GITHUB_TOKEN is not set")
        if not login or not all(ch.isalnum() or ch == "-" for ch in login) or len(login) > 39:
            fail("CONTRIB3D_USER must be a GitHub login")
        api = os.environ.get("CONTRIB3D_API_URL", "https://api.github.com/graphql").strip()
        if not api.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            fail("CONTRIB3D_API_URL must be an https URL")
        cal = fetch(api, token, login)

    if as_json:
        print(json.dumps(cal))
        return

    out = Path(args[0] if args else ".")
    out.mkdir(parents=True, exist_ok=True)
    for name, style, theme in (("3d-light.svg", light, "light"), ("3d-dark.svg", dark, "dark")):
        target = out / name
        tmp = target.with_suffix(".tmp")
        tmp.write_text(render(cal, style, theme), encoding="utf-8")
        tmp.replace(target)
    print(f"{cal['total']:,} contributions; light: {light}, dark: {dark}")


if __name__ == "__main__":
    main()
