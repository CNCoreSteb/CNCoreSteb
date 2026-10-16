#!/usr/bin/env python3
"""Contribution-graph snake, rendered as animated light/dark SVGs.

Standard library only: no pip installs, no third-party code at runtime.

Usage:  python3 -I snake.py OUTPUT_DIR

Environment:
  GITHUB_TOKEN      required; the workflow's own token is enough
  SNAKE_USER        GitHub login whose contribution graph to use (required)
  SNAKE_API_URL     default https://api.github.com/graphql
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

# geometry and timing (matches the familiar Platane/snk look)
CELL = 16          # grid pitch
DOT = 12           # cell size
DOT_R = 2          # cell corner radius
STEP_MS = 100      # one snake move
SNAKE_LEN = 4
ROWS = 7
EPS = 0.01         # percent, for "instant" colour changes

THEMES = {
    "light": {
        "file": "github-contribution-grid-snake.svg",
        "border": "#1b1f230a",
        "empty": "#ebedf0",
        "dots": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "snake": "purple",
    },
    "dark": {
        "file": "github-contribution-grid-snake-dark.svg",
        "border": "#1b1f230a",
        "empty": "#161b22",
        "dots": ["#161b22", "#01311f", "#034525", "#0f6d31", "#00c647"],
        "snake": "purple",
    },
}

LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
          "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

QUERY = """query($login:String!){user(login:$login){contributionsCollection{
contributionCalendar{weeks{contributionDays{weekday contributionLevel}}}}}}"""

Point = tuple[int, int]


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- data

def fetch_grid(api: str, token: str, login: str) -> tuple[int, dict[Point, int]]:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(api, data=body, method="POST", headers={
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "contrib-snake/1.0",
    })
    payload: dict = {}
    last_err = "no response"
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp) or {}
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                fail(f"GitHub rejected the token (HTTP {e.code})")
            last_err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last_err = type(e).__name__
        time.sleep(5 * (i + 1))
    else:
        fail(f"could not fetch contributions ({last_err})")

    if payload.get("errors"):
        fail("GraphQL error: " + "; ".join(str(e.get("message")) for e in payload["errors"])[:300])
    try:
        weeks = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    except (KeyError, TypeError):
        fail(f"no contribution calendar for user {login!r}")

    cells: dict[Point, int] = {}
    for x, week in enumerate(weeks):
        for day in week.get("contributionDays") or []:
            try:
                y = int(day["weekday"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= y < ROWS:
                cells[(x, y)] = LEVELS.get(day.get("contributionLevel"), 0)
    if not weeks:
        fail("empty contribution calendar")
    return len(weeks), cells


# ---------------------------------------------------------------- solver

def solve(width: int, cells: dict[Point, int]) -> tuple[list[tuple[Point, ...]], dict[Point, int]]:
    """Return the snake's state at every step and the step at which each cell is eaten.

    The snake clears the lightest cells first, treating darker uneaten cells as walls
    when it can, then returns to its starting pose so the loop is seamless.
    """
    remaining = {p: lvl for p, lvl in cells.items() if lvl > 0}
    snake: list[Point] = [(i, -1) for i in range(SNAKE_LEN)]  # head first
    start_pose = tuple(snake)
    chain: list[tuple[Point, ...]] = [start_pose]
    eaten: dict[Point, int] = {}

    def inside(p: Point) -> bool:
        return -1 <= p[0] <= width and -1 <= p[1] <= ROWS

    def bfs(start: Point, goals: set[Point], blocked: set[Point]) -> list[Point] | None:
        prev: dict[Point, Point | None] = {start: None}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            if cur in goals and cur != start:
                path = []
                while cur != start:
                    path.append(cur)
                    cur = prev[cur]  # type: ignore[assignment]
                return path[::-1]
            x, y = cur
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n not in prev and inside(n) and n not in blocked:
                    prev[n] = cur
                    queue.append(n)
        return None

    def walk(path: list[Point]) -> None:
        for p in path:
            snake.insert(0, p)
            snake.pop()
            chain.append(tuple(snake))
            if p in remaining:
                eaten[p] = len(chain) - 1
                del remaining[p]

    for level in range(1, 5):
        while True:
            goals = {p for p, lvl in remaining.items() if lvl == level}
            if not goals:
                break
            body = set(snake[:-1])
            darker = {p for p, lvl in remaining.items() if lvl > level}
            path = (bfs(snake[0], goals, body | darker)
                    or bfs(snake[0], goals, body)
                    or bfs(snake[0], goals, set()))
            if not path:
                break
            walk(path)

    # head home: arrive at the end of the start row from outside it, then slide in
    home_row = {(i, -1) for i in range(SNAKE_LEN)}
    entry: Point = (SNAKE_LEN, -1)
    if snake[0] != entry:
        path = (bfs(snake[0], {entry}, set(snake[:-1]) | home_row)
                or bfs(snake[0], {entry}, home_row)
                or bfs(snake[0], {entry}, set()))
        walk(path or [])
    walk([(i, -1) for i in range(SNAKE_LEN - 1, -1, -1)])

    if chain[-1] != start_pose:
        fail("internal error: snake did not return to its start pose")
    return chain, eaten


# ---------------------------------------------------------------- render

def pct(i: float, n: int) -> str:
    v = 100 * i / n
    return f"{v:.3f}".rstrip("0").rstrip(".") + "%"


def b36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if not n:
            return out


def render(width: int, cells: dict[Point, int], chain, eaten, theme: dict) -> str:
    n = len(chain) - 1
    duration = max(n, 1) * STEP_MS
    m = (CELL - DOT) / 2
    css: list[str] = []
    els: list[str] = []

    css.append(":root{" + f"--cb:{theme['border']};--cs:{theme['snake']};--ce:{theme['empty']};"
               + "".join(f"--c{i}:{c};" for i, c in enumerate(theme["dots"])) + "}")
    anim = f"animation:none {duration}ms linear infinite"

    # grid
    css.append(f".c{{shape-rendering:geometricPrecision;fill:var(--ce);stroke:var(--cb);"
               f"stroke-width:1px;{anim}}}")
    eat_order = sorted(eaten, key=lambda p: eaten[p])
    ids = {p: "c" + b36(i) for i, p in enumerate(eat_order)}
    for (x, y), lvl in sorted(cells.items()):
        cls = "c"
        if (x, y) in ids:
            cid = ids[(x, y)]
            t = 100 * eaten[(x, y)] / n
            css.append(f"@keyframes {cid}{{{t - EPS:.3f}%{{fill:var(--c{lvl})}}"
                       f"{t + EPS:.3f}%,to{{fill:var(--ce)}}}}")
            css.append(f".c.{cid}{{fill:var(--c{lvl});animation-name:{cid}}}")
            cls += " " + cid
        elif lvl:
            css.append(f".l{lvl}{{fill:var(--c{lvl})}}")
            cls += f" l{lvl}"
        els.append(f'<rect class="{cls}" x="{x * CELL + m:g}" y="{y * CELL + m:g}" '
                   f'width="{DOT}" height="{DOT}" rx="{DOT_R}"/>')

    # progress bar of eaten colours under the grid
    if eat_order:
        css.append(f".u{{transform-box:fill-box;transform-origin:left;transform:scaleX(0);{anim}}}")
        blocks: list[tuple[int, list[int]]] = []
        for p in eat_order:
            lvl = cells[p]
            if blocks and blocks[-1][0] == lvl:
                blocks[-1][1].append(eaten[p])
            else:
                blocks.append((lvl, [eaten[p]]))
        unit = width * CELL / len(eat_order)
        bx = 0.0
        bar_y = (ROWS + 2) * CELL
        for i, (lvl, steps) in enumerate(blocks):
            uid = "u" + b36(i)
            frames = []
            for j, s in enumerate(steps):
                t = 100 * s / n
                frames.append(f"{t - EPS:.3f}%{{transform:scaleX({j / len(steps):.3f})}}")
                frames.append(f"{t + EPS:.3f}%{{transform:scaleX({(j + 1) / len(steps):.3f})}}")
            frames.append("to{transform:scaleX(1)}")
            css.append(f"@keyframes {uid}{{{''.join(frames)}}}")
            css.append(f".u.{uid}{{fill:var(--c{lvl});animation-name:{uid}}}")
            els.append(f'<rect class="u {uid}" x="{bx:.1f}" y="{bar_y}" '
                       f'width="{len(steps) * unit + 0.6:.1f}" height="{DOT}"/>')
            bx += len(steps) * unit

    # snake: one rect per segment, keyframed only where its direction changes
    css.append(f".s{{shape-rendering:geometricPrecision;fill:var(--cs);{anim}}}")
    i_max = min(4, SNAKE_LEN)
    for k in range(SNAKE_LEN):
        u = (1 - min(k, i_max) / i_max) ** 2
        size = (1 - u) * DOT * 0.8 + u * CELL * 0.9
        off = (CELL - size) / 2
        r = min(4.5, 4 * size / DOT)
        pos = [state[k] for state in chain]
        keep = [0]
        for i in range(1, n):
            d1 = (pos[i][0] - pos[i - 1][0], pos[i][1] - pos[i - 1][1])
            d2 = (pos[i + 1][0] - pos[i][0], pos[i + 1][1] - pos[i][1])
            if d1 != d2:
                keep.append(i)
        if n:
            keep.append(n)
        frames = "".join(f"{pct(i, max(n, 1))}{{transform:translate({pos[i][0] * CELL}px,{pos[i][1] * CELL}px)}}"
                         for i in keep)
        sx, sy = pos[0]
        css.append(f"@keyframes s{k}{{{frames}}}")
        css.append(f".s.s{k}{{transform:translate({sx * CELL}px,{sy * CELL}px);animation-name:s{k}}}")
        els.append(f'<rect class="s s{k}" x="{off:.1f}" y="{off:.1f}" width="{size:.1f}" '
                   f'height="{size:.1f}" rx="{r:.1f}"/>')

    css.append("@media (prefers-reduced-motion:reduce){.c,.u,.s{animation:none}}")

    vb_w, vb_h = (width + 2) * CELL, (ROWS + 5) * CELL
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-CELL} {-2 * CELL} {vb_w} {vb_h}" '
            f'width="{vb_w}" height="{vb_h}">'
            "<desc>Contribution snake, generated by .github/scripts/snake.py</desc>"
            f"<style>{''.join(css)}</style>{''.join(els)}</svg>\n")


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    login = os.environ.get("SNAKE_USER", "").strip()
    if not token:
        fail("GITHUB_TOKEN is not set")
    if not login or not all(ch.isalnum() or ch == "-" for ch in login) or len(login) > 39:
        fail("SNAKE_USER must be a GitHub login")
    api = os.environ.get("SNAKE_API_URL", "https://api.github.com/graphql").strip()
    if not api.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        fail("SNAKE_API_URL must be an https URL")

    width, cells = fetch_grid(api, token, login)
    chain, eaten = solve(width, cells)

    out_dir.mkdir(parents=True, exist_ok=True)
    for theme in THEMES.values():
        target = out_dir / theme["file"]
        tmp = target.with_suffix(".tmp")
        tmp.write_text(render(width, cells, chain, eaten, theme), encoding="utf-8")
        tmp.replace(target)

    print(f"{len(eaten)} cells eaten in {len(chain) - 1} steps "
          f"({(len(chain) - 1) * STEP_MS / 1000:.0f}s loop)")


if __name__ == "__main__":
    main()
