#!/usr/bin/env python3
"""Draw the README's static banners: wave header, wave footer and the typing intro.

Standard library only. Run it again after changing the text below; the
SVGs are committed, so nothing here runs in a workflow.

Usage:  python3 -I banners.py OUTPUT_DIR      writes header.svg, footer.svg, typing.svg
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

TITLE = "CN_CoreSteb"
SUBTITLE = "Weather · Geospatial · AI"
LINES = [
    "Software Development @ QUT, Brisbane",
    "Building AtmoSeq — a weather analysis platform",
    "Geospatial · WebGL · LLM Agents",
    "Linux user since 2020",
]

NAVY, BLUE, SKY = "#0B1D3A", "#1E4E8C", "#3FA9F5"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"

W = 854
WAVE_SECONDS = 18


def wave(edge: tuple[float, ...], top: bool, h: int) -> str:
    """A band filled from the top (header) or bottom (footer) down/up to a wavy edge.

    edge = (y at left, 1st control, 2nd control, y at middle, 3rd control, y at right);
    the second half is a smooth continuation of the first.
    """
    a, b, c, d, e, f = edge
    base = 0 if top else h
    return (f"M0 {base} L0 {a} C140 {b} 280 {c} {W / 2} {d} S714 {e} {W} {f} L{W} {base} Z")


def layers(frames_a: list, frames_b: list, top: bool, h: int) -> str:
    out = []
    for frames, begin in ((frames_a, 0), (frames_b, -WAVE_SECONDS / 2)):
        values = ";".join(wave(f, top, h) for f in frames + [frames[0]])
        n = len(frames)
        times = ";".join(f"{i / n:.3f}" for i in range(n + 1))
        splines = ";".join(["0.45 0 0.55 1"] * n)
        out.append(f'<path fill="url(#g)" opacity=".45" d="{wave(frames[0], top, h)}">'
                   f'<animate attributeName="d" dur="{WAVE_SECONDS}s" begin="{begin}s" repeatCount="indefinite" '
                   f'calcMode="spline" keyTimes="{times}" keySplines="{splines}" values="{values}"/></path>')
    return "".join(out)


def gradient(stops: list[str]) -> str:
    return ('<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0">'
            + "".join(f'<stop offset="{i / (len(stops) - 1):g}" stop-color="{c}"/>' for i, c in enumerate(stops))
            + "</linearGradient></defs>")


def header() -> str:
    h = 200
    a = [(130, 175, 115, 140, 170, 125), (150, 110, 165, 135, 105, 155), (120, 160, 150, 120, 165, 140)]
    b = [(145, 115, 170, 155, 120, 160), (125, 170, 125, 150, 175, 130), (160, 130, 140, 165, 135, 120)]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
            f'role="img" aria-label="{escape(TITLE)}: {escape(SUBTITLE)}">'
            f"<style>text{{font-family:{SANS};fill:#FFF}}"
            ".t{font-size:64px;font-weight:700}.d{font-size:18px;font-weight:500}"
            ".t,.d{opacity:0;animation:in 1.2s ease-in-out forwards}.d{animation-delay:.3s}"
            "@keyframes in{to{opacity:1}}"
            "@media (prefers-reduced-motion:reduce){.t,.d{animation:none;opacity:1}}</style>"
            + gradient([NAVY, BLUE, SKY]) + layers(a, b, True, h)
            + f'<text class="t" x="50%" y="72" text-anchor="middle" dominant-baseline="middle">{escape(TITLE)}</text>'
            f'<text class="d" x="50%" y="116" text-anchor="middle" dominant-baseline="middle">{escape(SUBTITLE)}</text>'
            "</svg>\n")


def footer() -> str:
    h = 120
    a = [(55, 10, 70, 45, 15, 60), (35, 80, 25, 60, 85, 35), (65, 25, 35, 65, 20, 45)]
    b = [(40, 70, 15, 30, 65, 25), (60, 15, 60, 35, 10, 55), (25, 55, 45, 20, 50, 65)]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
            f'role="img" aria-label="footer wave">'
            + gradient([NAVY, BLUE, SKY]) + layers(a, b, False, h) + "</svg>\n")


def typing() -> str:
    """Type each line, hold, erase, pause; one line at a time, looping.

    The text is stretched to an exact width (textLength), so a clip that
    widens one character cell at a time reveals it letter by letter in any
    monospace font the viewer has.
    """
    w, h, size, cw, base = 600, 50, 20, 12, 32
    type_ms, hold_ms, erase_ms, pause_ms = 55, 1800, 14, 500

    events: list[tuple[int, int, int]] = []  # (time ms, line, visible chars)
    t = 0
    for i, line in enumerate(LINES):
        n = len(line)
        for k in range(1, n + 1):
            events.append((t + k * type_ms, i, k))
        t += n * type_ms + hold_ms
        for k in range(n - 1, -1, -1):
            t += erase_ms
            events.append((t, i, k))
        t += pause_ms
    total = t

    def key(ms: int) -> str:
        return f"{ms / total:.5f}"

    body = []
    cursor_x = [(0, (w - len(LINES[0]) * cw) / 2)]
    for i, line in enumerate(LINES):
        n = len(line)
        left = (w - n * cw) / 2
        steps = [(0, 0)] + [(ms, k * cw) for ms, li, k in events if li == i]
        times = ";".join(key(ms) for ms, _ in steps)
        values = ";".join(f"{v:g}" for _, v in steps)
        body.append(f'<clipPath id="c{i}"><rect x="{left:g}" y="0" height="{h}" width="0">'
                    f'<animate attributeName="width" dur="{total}ms" repeatCount="indefinite" calcMode="discrete" '
                    f'keyTimes="{times}" values="{values}"/></rect></clipPath>')
        body.append(f'<text clip-path="url(#c{i})" x="{left:g}" y="{base}" textLength="{n * cw}" '
                    f'lengthAdjust="spacingAndGlyphs">{escape(line)}</text>')
        cursor_x += [(ms, left + k * cw) for ms, li, k in events if li == i]

    cursor_x.sort()
    times = ";".join(key(ms) for ms, _ in cursor_x)
    values = ";".join(f"{x:g}" for _, x in cursor_x)
    body.append(f'<rect class="cur" x="{cursor_x[0][1]:g}" y="{base - 17}" width="2" height="22" fill="#2F81F7">'
                f'<animate attributeName="x" dur="{total}ms" repeatCount="indefinite" calcMode="discrete" '
                f'keyTimes="{times}" values="{values}"/></rect>')

    label = " / ".join(LINES)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{escape(label)}">'
            f"<style>text{{font-family:{MONO};font-size:{size}px;font-weight:500;fill:#2F81F7}}"
            ".cur{animation:blink 1s steps(1) infinite}@keyframes blink{50%{opacity:0}}</style>"
            + "".join(body) + "</svg>\n")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    for name, svg in (("header.svg", header()), ("footer.svg", footer()), ("typing.svg", typing())):
        (out / name).write_text(svg, encoding="utf-8")
    print(f"wrote header.svg, footer.svg, typing.svg to {out}")


if __name__ == "__main__":
    main()
