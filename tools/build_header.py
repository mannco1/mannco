#!/usr/bin/env python3
"""Build assets/header.svg: the animated amoCRM pipeline on the GitHub profile.

    python tools/build_header.py

A deal card travels through five pipeline stages, each with its own gag, and
ends pinned, stamped and paid. GitHub shows the SVG through <img>, so the file
is self-contained: CSS keyframes only, no scripts, fonts subset and inlined.

Fonts: @fontsource/unbounded and @fontsource/onest unpacked under tools/fonts
(`npm pack @fontsource/unbounded @fontsource/onest`, then unpack). If they are
missing, the same tarballs are downloaded from the npm registry.
Needs: pip install fonttools brotli
"""

from __future__ import annotations

import base64
import io
import itertools
import json
import math
import random
import re
import tarfile
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset as ftsubset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "header.svg"
FONT_DIR = ROOT / "tools" / "fonts"
FONT_PACKAGES = ("@fontsource/unbounded", "@fontsource/onest")
SUBSET_ORDER = ("latin", "cyrillic", "latin-ext", "cyrillic-ext")
MAX_BYTES = 120 * 1024
LOOP = 30  # seconds; every animation except the typing dots runs on this timeline

# ── palette: dark, like the rest of the profile ────────────────────────────
BG = ("#0a0f1f", "#121a36")
ACCENT = ("#00ADD8", "#7B61FF", "#FF6AD5")
INK = "#e6edf3"
MUTED = "#9aa7c2"
CARD = "#1c2547"
LINE = "#2a3563"
RAISED = "#2e3a6c"  # flap, plates, sheet tabs
GHOST, GHOST_LINE, GHOST_BARS = "#141c3a", "#1f2a4d", ("#26305a", "#1f2850")
STAGE_COLORS = ("#7d89a6", "#00ADD8", "#7B61FF", "#FF6AD5", "#3ddc97")
GREEN = "#3ddc97"
RED = "#FF6B6B"
BADGE_RED = "#C93838"
PIN_RED = "#E5484D"
COFFEE = "#C8A27C"

# ── layout ──────────────────────────────────────────────────────────────────
W = 1000
PAD, COL_W, GAP = 30, 180, 10
STEP = COL_W + GAP
COL_X = [PAD + i * STEP for i in range(5)]
BAR_Y, STAGE_Y, COUNT_Y = 118, 141, 159
CARD_Y, CARD_W = 188, COL_W
TEXT_X, TEXT_PAD_R = 14, 10
INNER_W = CARD_W - TEXT_X - TEXT_PAD_R
CAP_Y = (26, 44)         # stage caption, one or two lines
URGENCY_Y = 64
ROW_Y, ROW_H = 74, 26    # the per-stage bottom row
CARD_H = ROW_Y + ROW_H + 14  # sized for the busiest stage
GHOST_Y = CARD_Y + CARD_H + 10
H = GHOST_Y + 30         # the ghost row is cut by the bottom edge
FOLD = 14
# Rotating groups hold this invisible rect so that their fill-box, and with it
# transform-origin 50% 50%, is always the card centre, whatever pokes out.
PIVOT = f'<rect x="-120" y="-120" width="{CARD_W + 240}" height="{CARD_H + 240}" fill="none"/>'
FILL_BOX = "transform-box:fill-box;transform-origin:50% 50%"

# ── copy ────────────────────────────────────────────────────────────────────
STAGES = ("Неразобранное", "Первичный контакт", "Переговоры",
          "Принимают решение", "Успешно реализовано")
BASE_COUNTS = ((2, 90_000), (3, 240_000), (2, 310_000), (1, 180_000), (12, 1_800_000))
CAPTIONS = (("Нам бы CRM,", "как у всех"), ("Выясняем, что нужно", "на самом деле"),
            ("ТЗ_final_v3_", "точно_final"), ("А можно ещё", "одну кнопку?"),
            ("Фиксируем прибыль",))
ROLE = "BA, PM/PdM в сфере разработки ПО и интеграции amoCRM"
TAGLINE = "Внедряю amoCRM: от ТЗ до прибыли"
URGENCY = (None, "(срочно)", "(очень срочно)", "(очень-очень срочно)", None)
DEAL_STEPS = (0, 37_500, 75_000, 112_500, 150_000)  # the last column's sum runs up by these

# ── timeline, seconds ───────────────────────────────────────────────────────
T = {
    "land": 0.42,
    "dups": (0.5, 0.7),
    "buttons": (0.9, 1.1),
    "merge": (3.3, 3.8),
    "typing": (4.9, 8.3),
    "sheets": (9.4, 9.8),
    "edits": (10.5, 11.5),
    "coffee": 12.0,
    "pills": (14.6, 15.1, 15.6, 16.1, 16.6),
    "shake": 16.7,
    "overdue": 17.0,
    "pin": (19.6, 19.9),
    "stamp": (20.3, 20.52),
    "check": 20.7,
    "ticks": (20.8, 21.0, 21.2, 21.4, 21.6),
    "fade": (28.0, 28.8),
}
MOVES = ((3.9, 4.7), (8.4, 9.2), (13.4, 14.2), (18.6, 19.4))
SWITCH = [(a + b) / 2 for a, b in MOVES]  # stage texts swap mid-move
STAGE_SPANS = list(zip([0, *SWITCH], [*SWITCH, LOOP]))
PRESENCE = [(T["land"], SWITCH[0])] + list(zip(SWITCH, SWITCH[1:]))
RESET = T["fade"][0] + 0.2  # the last column drops back to its base count as the card fades
TICK_FADE = 0.06
EASE = {
    "lin": "linear",
    "out": "cubic-bezier(.2,.8,.2,1)",
    "in": "cubic-bezier(.55,0,1,.45)",
    "io": "cubic-bezier(.65,0,.35,1)",
}

STYLES = {  # class: family, weight, size, fill, letter-spacing
    "name": ("Unbounded", 700, 52, "url(#accent)", 0),
    "role": ("Onest", 500, 18, INK, 0),
    "tagline": ("Onest", 400, 15, MUTED, 0),
    "stage": ("Onest", 500, 13.5, INK, 0),
    "count": ("Onest", 400, 12, MUTED, 0),
    "cap": ("Onest", 500, 14.5, INK, 0),
    "urgency": ("Onest", 500, 12, INK, 0),
    "note": ("Onest", 400, 11.5, MUTED, 0),
    "btn": ("Onest", 500, 11, None, 0),
    "chip": ("Onest", 500, 10.5, INK, 0),
    "stamp": ("Unbounded", 700, 14, GREEN, 1),
}


# ── numbers and words ───────────────────────────────────────────────────────
def num(v: float, nd: int = 2) -> str:
    s = f"{round(float(v), nd) + 0.0:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def money(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def plural(n: int, forms=("сделка", "сделки", "сделок")) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def counter(n: int, total: int) -> str:
    return f"{n} {plural(n)}: {money(total)} ₽"


def tf(x: float = 0, y: float = 0, r: float = 0, s: float = 1) -> str:
    """One transform shape for every keyframe, so browsers interpolate it piecewise."""
    return f"translate({num(x)}px,{num(y)}px) rotate({num(r)}deg) scale({num(s)})"


# ── fonts ───────────────────────────────────────────────────────────────────
def ensure_fonts() -> None:
    """Fetch the @fontsource tarballs (what `npm pack` would) if they are missing."""
    for pkg in FONT_PACKAGES:
        family = pkg.split("/")[1]
        if any(FONT_DIR.glob(f"**/files/{family}-*-normal.woff2")):
            continue
        with urllib.request.urlopen(f"https://registry.npmjs.org/{pkg}/latest") as r:
            tarball = json.load(r)["dist"]["tarball"]
        with urllib.request.urlopen(tarball) as r:
            data = r.read()
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(FONT_DIR / family, filter="data")
        print(f"fetched {tarball}")


class Fonts:
    """Finds the @fontsource file for every character and subsets what is used."""

    def __init__(self) -> None:
        self._faces: dict[tuple[str, int], list[tuple[Path, TTFont, dict]]] = {}
        self.used: dict[tuple[str, int], set[str]] = {}

    def faces(self, family: str, weight: int):
        key = (family, weight)
        if key not in self._faces:
            slug, tail = family.lower(), f"-{weight}-normal.woff2"
            found = {p.name[len(slug) + 1:-len(tail)]: p
                     for p in FONT_DIR.glob(f"**/files/{slug}-*{tail}")}
            order = sorted(found, key=lambda s: (SUBSET_ORDER.index(s)
                                                 if s in SUBSET_ORDER else len(SUBSET_ORDER), s))
            if not order:
                raise SystemExit(f"no {family} {weight} files under {FONT_DIR}")
            faces = []
            for sub in order:
                font = TTFont(found[sub])
                faces.append((found[sub], font, font.getBestCmap()))
            self._faces[key] = faces
        return self._faces[key]

    def face_for(self, family: str, weight: int, ch: str):
        for face in self.faces(family, weight):
            if ord(ch) in face[2]:
                return face
        raise SystemExit(f"{ch!r} (U+{ord(ch):04X}) is in no {family} {weight} file")

    def width(self, s: str, style: str) -> float:
        family, weight, size, _, ls = STYLES[style]
        total = 0.0
        for ch in s:
            _, font, cmap = self.face_for(family, weight, ch)
            advance = font["hmtx"][cmap[ord(ch)]][0]
            total += advance / font["head"].unitsPerEm * size + ls
        return total

    def use(self, s: str, style: str) -> str:
        family, weight = STYLES[style][:2]
        self.used.setdefault((family, weight), set()).update(s)
        for ch in s:
            self.face_for(family, weight, ch)
        return escape(s)

    def css(self) -> str:
        rules = []
        for (family, weight), chars in sorted(self.used.items()):
            by_file: dict[Path, list[str]] = {}
            for ch in sorted(chars):
                by_file.setdefault(self.face_for(family, weight, ch)[0], []).append(ch)
            for path, chs in by_file.items():
                data = base64.b64encode(subset_woff2(path, chs)).decode()
                rules.append(f"@font-face{{font-family:'{family}';font-weight:{weight};"
                             f"src:url(data:font/woff2;base64,{data}) format('woff2');"
                             f"unicode-range:{unicode_range(chs)}}}")
        return "".join(rules)


def subset_woff2(path: Path, chars: list[str]) -> bytes:
    font = TTFont(path)
    options = ftsubset.Options()
    options.flavor = "woff2"
    options.hinting = False
    options.desubroutinize = True
    subsetter = ftsubset.Subsetter(options)
    subsetter.populate(unicodes=[ord(c) for c in chars])
    subsetter.subset(font)
    buf = io.BytesIO()
    font.flavor = "woff2"
    font.save(buf)
    return buf.getvalue()


def unicode_range(chars: list[str]) -> str:
    cps = sorted({ord(c) for c in chars})
    ranges, start, prev = [], cps[0], cps[0]
    for cp in cps[1:] + [None]:
        if cp is not None and cp == prev + 1:
            prev = cp
            continue
        ranges.append(f"U+{start:X}" if start == prev else f"U+{start:X}-{prev:X}")
        if cp is not None:
            start = prev = cp
    return ",".join(ranges)


FONTS = Fonts()


def width(s: str, style: str) -> float:
    return FONTS.width(s, style)


def fits(s: str, style: str, room: float, where: str) -> float:
    w = width(s, style)
    assert w <= room, f"{where}: {s!r} is {w:.1f}px wide, room for {room:.1f}px"
    return w


def text(x: float, y: float, s: str, style: str, *, anchor: str = "",
         fill: str = "", cls: str = "") -> str:
    attrs = f' text-anchor="{anchor}"' if anchor else ""
    attrs += f' style="fill:{fill}"' if fill else ""
    classes = f"{style} {cls}".strip()
    return f'<text x="{num(x)}" y="{num(y)}" class="{classes}"{attrs}>{FONTS.use(s, style)}</text>'


# ── one timeline ────────────────────────────────────────────────────────────
def pct(t: float) -> str:
    return num(100 * t / LOOP, 3) + "%"


def normalize(pts: list) -> list[tuple[float, str, str]]:
    frames = sorted(((p[0], p[1] if isinstance(p[1], str) else num(p[1]),
                      p[2] if len(p) > 2 else "lin") for p in pts), key=lambda f: f[0])
    if frames[0][0] > 0:
        frames.insert(0, (0, frames[0][1], "lin"))
    if frames[-1][0] < LOOP:
        frames.append((LOOP, frames[-1][1], "lin"))
    keys = [pct(f[0]) for f in frames]
    assert len(set(keys)) == len(keys), f"two keyframes share a moment: {keys}"
    assert frames[0][0] >= 0 and frames[-1][0] <= LOOP, "keyframe outside the loop"
    return frames


class Timeline:
    """Collects CSS animations; each track is [(seconds, value, easing-to-next)]."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.keyframes: list[str] = []
        self.rules: list[str] = []
        self.base: list[str] = []

    def __call__(self, tracks: dict[str, list], base: str = "", seam: bool = True) -> str:
        cls = f"a{next(self._ids)}"
        anims = []
        for prop, pts in tracks.items():
            frames = normalize(pts)
            if seam:
                assert frames[0][1] == frames[-1][1], f".{cls} {prop} jumps at the loop seam"
            name = f"k{next(self._ids)}"
            body = "".join(
                f"{pct(t)}{{{prop}:{v}"
                + (f";animation-timing-function:{EASE[e]}" if e != "lin" else "") + "}"
                for t, v, e in frames)
            self.keyframes.append(f"@keyframes {name}{{{body}}}")
            anims.append(f"{name} {LOOP}s linear infinite")
        self.rules.append(f".{cls}{{animation:{','.join(anims)}}}")
        if base:
            self.base.append(f".{cls}{{{base}}}")
        return cls


TL = Timeline()


def show(spans, fade=0.2) -> list:
    """Opacity track: visible during each (start, end) span, crossfading at the edges.

    `fade` is a duration centred on each edge, or a function of the edge time
    returning how long the crossfade runs (before, after) it.
    """
    def around(t):
        return fade(t) if callable(fade) else (fade / 2, fade / 2)
    pts = []
    for start, end in spans:
        before, after = around(start)
        pts += [(0, 1)] if start <= 0 else [(start - before, 0), (start + after, 1)]
        before, after = around(end)
        pts += [(LOOP, 1)] if end >= LOOP else [(end - before, 1), (end + after, 0)]
    return pts


def tick_fade(t: float) -> tuple[float, float]:
    """Running numbers land exactly on their tick; other swaps crossfade around the edge."""
    return (TICK_FADE, 0) if any(abs(t - x) < 1e-6 for x in T["ticks"]) else (0.1, 0.1)


def pop(t0: float, peak: float = 1.15) -> list:
    return [(0, tf(s=0)), (t0, tf(s=0), "out"), (t0 + 0.18, tf(s=peak)), (t0 + 0.3, tf(s=1))]


# ── static scenery ──────────────────────────────────────────────────────────
def defs() -> str:
    rnd = random.Random(3)
    spots = "".join(
        f'<circle cx="{num(rnd.uniform(-62, 62))}" cy="{num(rnd.uniform(-16, 16))}" '
        f'r="{num(rnd.uniform(.6, 2.3))}" fill="#000" fill-opacity="{num(rnd.uniform(.45, 1))}"/>'
        for _ in range(34))
    fold = TL({"transform": [(0, tf(s=0)), (MOVES[1][0], tf(s=0), "io"), (MOVES[1][1], tf(s=1)),
                             (MOVES[2][0], tf(s=1), "io"), (MOVES[2][1], tf(s=0))]},
              base=f"transform-box:fill-box;transform-origin:100% 0;transform:{tf(s=0)}")
    stops = "".join(f'<stop offset="{num(i / 2)}" stop-color="{c}"/>' for i, c in enumerate(ACCENT))
    return f"""<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG[0]}"/><stop offset="1" stop-color="{BG[1]}"/></linearGradient>
<linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">{stops}</linearGradient>
<linearGradient id="border" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{ACCENT[0]}" stop-opacity=".9"/><stop offset=".5" stop-color="{ACCENT[1]}" stop-opacity=".5"/><stop offset="1" stop-color="{ACCENT[2]}" stop-opacity=".9"/></linearGradient>
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="40"/></filter>
<filter id="shadow" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#000" flood-opacity=".45"/></filter>
<clipPath id="frame"><rect width="{W}" height="{H}" rx="18"/></clipPath>
<clipPath id="cardclip"><rect width="{CARD_W}" height="{CARD_H}" rx="6"/></clipPath>
<mask id="fold" maskUnits="userSpaceOnUse" x="-40" y="-40" width="{CARD_W + 80}" height="{CARD_H + 80}"><rect x="-40" y="-40" width="{CARD_W + 80}" height="{CARD_H + 80}" fill="#fff"/><path class="{fold}" d="M{CARD_W - FOLD} 0H{CARD_W}V{FOLD}Z" fill="#000"/></mask>
<mask id="grunge" maskUnits="userSpaceOnUse" x="-80" y="-30" width="160" height="60"><rect x="-80" y="-30" width="160" height="60" fill="#fff"/>{spots}</mask>
</defs>""", fold


def scenery() -> str:
    return (f'<rect width="{W}" height="{H}" fill="url(#bg)"/>'
            f'<circle cx="140" cy="{H}" r="150" fill="{ACCENT[0]}" opacity=".35" filter="url(#glow)"/>'
            f'<circle cx="900" cy="0" r="170" fill="{ACCENT[1]}" opacity=".4" filter="url(#glow)"/>')


def header() -> str:
    right = W - PAD
    left_edge = right - max(width(ROLE, "role"), width(TAGLINE, "tagline"))
    assert PAD + width("Mannco", "name") + 24 <= left_edge, "the name runs into the role"
    return (text(PAD, 82, "Mannco", "name") + text(right, 56, ROLE, "role", anchor="end")
            + text(right, 82, TAGLINE, "tagline", anchor="end"))


def columns() -> str:
    out = []
    for i, title in enumerate(STAGES):
        x = COL_X[i]
        fits(title, "stage", COL_W, "stage name")
        out.append(f'<rect x="{x}" y="{BAR_Y}" width="{COL_W}" height="4" rx="2" fill="{STAGE_COLORS[i]}"/>')
        out.append(text(x, STAGE_Y, title, "stage"))
        out.append(counters(i))
        bars = (96, 120, 84, 110, 102)[i]
        out.append(f'<rect x="{x}" y="{GHOST_Y}" width="{COL_W}" height="70" rx="6" fill="{GHOST}" stroke="{GHOST_LINE}"/>'
                   f'<rect x="{x + 14}" y="{GHOST_Y + 13}" width="{bars}" height="7" rx="3.5" fill="{GHOST_BARS[0]}"/>'
                   f'<rect x="{x + 14}" y="{GHOST_Y + 27}" width="{bars // 2 + 8}" height="6" rx="3" fill="{GHOST_BARS[1]}"/>')
    return "".join(out)


def counters(i: int) -> str:
    """One text per counter state; the card adds a deal while it stands in the column."""
    n, total = BASE_COUNTS[i]
    if i < 4:
        start, end = PRESENCE[i]
        states = [(counter(n, total), [(0, start), (end, LOOP)], True),
                  (counter(n + 1, total), [(start, end)], False)]
    else:
        ticks = T["ticks"]
        states = [(counter(n, total), [(0, ticks[0]), (RESET, LOOP)], False)]
        for k, t in enumerate(ticks):
            end = ticks[k + 1] if k + 1 < len(ticks) else RESET
            states.append((counter(n + 1, total + DEAL_STEPS[k]), [(t, end)], k == len(ticks) - 1))
    out = []
    for label, spans, final in states:
        fits(label, "count", COL_W, "column counter")
        cls = TL({"opacity": show(spans, tick_fade)}, base=f"opacity:{int(final)}")
        out.append(text(COL_X[i], COUNT_Y, label, "count", cls=cls))
    return "".join(out)


# ── the card ────────────────────────────────────────────────────────────────
def staged(i: int, fade: float = 0.2) -> str:
    """Class for something shown only while the card is in stage i (0-based)."""
    return TL({"opacity": show([STAGE_SPANS[i]], fade)},
              base=f"opacity:{int(i == 4)}", seam=False)


def dups() -> str:
    """Stage 1: two duplicates fan out behind the card, then merge back into it."""
    out = []
    under = tf(r=-4)
    merge0, merge1 = T["merge"]
    for i, (off, rot) in enumerate(((6, -2), (12, 3))):
        t0 = T["dups"][i]
        pose = tf(off, off, rot)
        cls = TL({"transform": [(0, under), (t0, under, "out"), (t0 + 0.25, pose),
                                (merge0, pose, "in"), (merge0 + 0.4, under)],
                  "opacity": [(0, 0), (t0, 0), (t0 + 0.12, 1), (merge0 + 0.2, 1), (merge1, 0)]},
                 base=f"{FILL_BOX};opacity:0", seam=False)
        plate_w = width("дубль", "chip") + 12
        plate_y = 36 + 50 * i
        out.append(
            f'<g class="{cls}">{PIVOT}'
            f'<rect width="{CARD_W}" height="{CARD_H}" rx="6" fill="#18203f" stroke="{LINE}"/>'
            f'<rect x="{CARD_W - 2}" y="{plate_y}" width="{num(plate_w)}" height="16" rx="4" fill="{RAISED}" stroke="#3b4880"/>'
            + text(CARD_W - 2 + plate_w / 2, plate_y + 11.8, "дубль", "chip", anchor="middle")
            + "</g>")
    return "".join(out)


def sheets() -> str:
    """Stage 3: earlier versions of the spec peek out from under the card.

    v1 lies right under the card and v2 fans out further behind it, so both
    tabs stay visible.
    """
    out = []
    hide = MOVES[2][0]
    for i, (t0, pose) in enumerate(zip(T["sheets"], (tf(9, -6, 3), tf(16, -10, 6)))):
        cls = TL({"transform": [(0, tf()), (t0, tf(), "out"), (t0 + 0.35, pose),
                                (hide, pose, "in"), (hide + 0.5, tf())],
                  "opacity": [(0, 0), (t0, 0), (t0 + 0.15, 1), (hide, 1), (hide + 0.4, 0)]},
                 base=f"{FILL_BOX};opacity:0", seam=False)
        tab_y = 14 + 26 * i
        label = f"v{i + 1}"
        fits(label, "chip", 26 - 8, "sheet tab")
        out.append(
            f'<g class="{cls}">{PIVOT}'
            f'<rect x="{CARD_W - 4}" y="{tab_y}" width="28" height="16" rx="4" fill="{RAISED}" stroke="#3b4880"/>'
            f'<rect width="{CARD_W}" height="{CARD_H}" rx="6" fill="#222b52" stroke="#34407a"/>'
            + text(CARD_W + 11, tab_y + 11.8, label, "chip", anchor="middle")
            + "</g>")
    return "".join(reversed(out))


def body(fold: str) -> str:
    stripe = [(0, STAGE_COLORS[0])]
    for i, s in enumerate(SWITCH):
        stripe += [(s - 0.15, STAGE_COLORS[i]), (s + 0.15, STAGE_COLORS[i + 1])]
    stripe_cls = TL({"fill": stripe}, base=f"fill:{STAGE_COLORS[-1]}", seam=False)
    return (f'<g mask="url(#fold)"><rect width="{CARD_W}" height="{CARD_H}" rx="6" fill="{CARD}" '
            f'stroke="{LINE}" filter="url(#shadow)"/></g>'
            f'<path class="{fold}" d="M{CARD_W - FOLD} 0V{FOLD}H{CARD_W}Z" fill="{RAISED}" '
            f'stroke="#3b4880" stroke-linejoin="round"/>'
            f'<rect class="{stripe_cls}" width="4" height="{CARD_H}" clip-path="url(#cardclip)"/>')


def captions() -> str:
    out = []
    for i, lines in enumerate(CAPTIONS):
        spans = ""
        for k, line in enumerate(lines):
            fits(line, "cap", INNER_W, f"stage {i + 1} caption")
            spans += f'<tspan x="{TEXT_X}" y="{CAP_Y[k]}">{FONTS.use(line, "cap")}</tspan>'
        out.append(f'<text class="cap {staged(i)}">{spans}</text>')
    return "".join(out)


def urgency_lines() -> str:
    out = []
    for i, urgency in enumerate(URGENCY):
        if urgency:
            fits(urgency, "urgency", INNER_W, "urgency")
            out.append(text(TEXT_X, URGENCY_Y, urgency, "urgency", cls=staged(i),
                            fill=RED if i == 3 else ""))
    return "".join(out)


def buttons() -> str:
    """Stage 1: accept / decline; accept gets pressed before the card moves on."""
    merge0, merge1 = T["merge"]
    x, out = TEXT_X, []
    for k, (label, fill, stroke, ink) in enumerate((("Принять", GREEN, GREEN, GREEN),
                                                     ("Отклонить", "none", "#4a5680", MUTED))):
        w = width(label, "btn") + 20
        track = pop(T["buttons"][k], 1.1)
        if k == 0:
            track += [(merge0 + 0.05, tf(), "out"), (merge0 + 0.15, tf(s=0.94), "out"), (merge0 + 0.3, tf())]
        cls = TL({"transform": track}, base=FILL_BOX, seam=False)
        fill_opacity = ' fill-opacity=".16"' if fill != "none" else ""
        out.append(f'<g class="{cls}"><rect x="{num(x)}" y="{ROW_Y + 2}" width="{num(w)}" height="20" rx="6" '
                   f'fill="{fill}"{fill_opacity} stroke="{stroke}" stroke-opacity=".6"/>'
                   + text(x + w / 2, ROW_Y + 16, label, "btn", anchor="middle", fill=ink) + "</g>")
        x += w + 6
    assert x - 6 <= TEXT_X + INNER_W, "buttons do not fit the card"
    row = TL({"opacity": [(0, 1), (merge0 + 0.3, 1), (merge1, 0)]}, base="opacity:0", seam=False)
    return f'<g class="{row}">{"".join(out)}</g>'


def typing() -> str:
    """Stage 2: the client is typing; the dots run on their own short period."""
    label = "клиент печатает"
    w = width(label, "note")
    dots = "".join(f'<circle class="dot dot{k + 1}" cx="{num(TEXT_X + w + 6 + 6 * k)}" cy="{ROW_Y + 12}" '
                   f'r="1.9" fill="{MUTED}"/>' for k in range(3))
    assert TEXT_X + w + 6 + 12 + 2 <= TEXT_X + INNER_W, "typing row does not fit"
    row = TL({"opacity": show([T["typing"]])}, base="opacity:0")
    return f'<g class="{row}">{text(TEXT_X, ROW_Y + 15, label, "note")}{dots}</g>'


def edits() -> str:
    """Stage 3: a document icon and a revision counter that keeps growing."""
    icon = (f'<g transform="translate({TEXT_X},{ROW_Y + 4})" fill="none" stroke="{MUTED}" stroke-width="1.2" '
            f'stroke-linejoin="round"><path d="M0 0H7.5L11 3.5V14H0Z"/><path d="M7.5 0V3.5H11"/>'
            f'<path d="M2.5 7H8.5M2.5 10H7" stroke-linecap="round"/></g>')
    e13, e14 = T["edits"]
    out = []
    for n, span in ((12, (0, e13)), (13, (e13, e14)), (14, (e14, LOOP))):
        label = f"Правок: {n}"
        fits(label, "note", INNER_W - 18, "revisions")
        cls = TL({"opacity": show([span], 0.12)}, base=f"opacity:{int(n == 14)}", seam=False)
        out.append(text(TEXT_X + 18, ROW_Y + 15, label, "note", cls=cls))
    row = TL({"opacity": show([(MOVES[1][1], MOVES[2][0])])}, base="opacity:0")
    return f'<g class="{row}">{icon}{"".join(out)}</g>'


def coffee() -> str:
    t0, hide = T["coffee"], MOVES[2][0]
    cls = TL({"opacity": [(0, 0), (t0, 0), (t0 + 0.8, 1), (hide, 1), (hide + 0.4, 0)]}, base="opacity:0")
    ring = (f'<g fill="none" stroke="{COFFEE}" opacity=".45">'
            f'<ellipse rx="15" ry="13" stroke-width="2.6" transform="rotate(-8)"/>'
            f'<ellipse cx=".8" cy="-.6" rx="13.2" ry="11.6" stroke-width="1.1" opacity=".7" transform="rotate(14)"/>'
            f'<path d="M-13.5 5.5Q-17 9-14 12.5" stroke-width="1.6" stroke-linecap="round"/>'
            f'<circle cx="15.5" cy="-10" r="1.4" fill="{COFFEE}" stroke="none"/>'
            f'<circle cx="-9" cy="-14" r=".9" fill="{COFFEE}" stroke="none"/></g>')
    return f'<g transform="translate({CARD_W - 33},{ROW_Y + 9})"><g class="{cls}">{ring}</g></g>'


def pills() -> str:
    """Stage 4: one more button, and another; the last one no longer fits."""
    widths, gap, h = (28, 40, 24, 34, 60), 6, 14
    fall0 = MOVES[3][0]
    x, out = TEXT_X, []
    for k, (w, t0) in enumerate(zip(widths, T["pills"])):
        color = STAGE_COLORS[k]
        if k < len(widths) - 1:
            assert x + w <= TEXT_X + INNER_W, f"pill {k + 1} should fit the card"
        else:
            assert x + w > CARD_W + 20, "the last pill must stick out of the card"
        drop = fall0 + 0.05 * k
        spin = (-28, 22, -18, 30, -24)[k]
        cls = TL({"transform": pop(t0) + [(drop, tf(), "in"), (drop + 0.5, tf(0, 46, spin))],
                  "opacity": [(0, 0), (t0, 0), (t0 + 0.01, 1), (drop + 0.2, 1), (drop + 0.5, 0)]},
                 base=f"{FILL_BOX};opacity:0", seam=False)
        out.append(f'<g class="{cls}"><rect x="{x}" y="{ROW_Y + 5}" width="{w}" height="{h}" rx="7" fill="{color}"/>'
                   f'<rect x="{x + 6}" y="{ROW_Y + 10}" width="{w - 12}" height="4" rx="2" fill="#0a0f1f" opacity=".35"/></g>')
        x += w + gap
    return "".join(out)


def overdue() -> str:
    label = "Просрочено"
    w, h = width(label, "chip") + 14, 17
    x, y = CARD_W + 6 - w, -8
    t0, hide = T["overdue"], MOVES[3][0]
    cls = TL({"transform": pop(t0) + [(hide, tf(), "in"), (hide + 0.3, tf(s=0))],
              "opacity": [(0, 0), (t0, 0), (t0 + 0.02, 1), (hide + 0.1, 1), (hide + 0.3, 0)]},
             base=f"{FILL_BOX};opacity:0", seam=False)
    return (f'<g transform="translate({num(x)},{y})"><g class="{cls}">'
            f'<rect width="{num(w)}" height="{h}" rx="{h / 2}" fill="{BADGE_RED}"/>'
            + text(w / 2, 12, label, "chip", anchor="middle", fill="#fff") + "</g></g>")


def stamp() -> str:
    """Stage 5: ОПЛАЧЕНО slams onto the card; a grunge mask makes it an imprint."""
    label = "ОПЛАЧЕНО"
    tw = width(label, "stamp")
    w, h, angle = tw + 16, 26, -10
    cx, cy = CARD_W / 2, ROW_Y + 14
    a = math.radians(angle)
    half_w = abs(w / 2 * math.cos(a)) + abs(h / 2 * math.sin(a))
    half_h = abs(w / 2 * math.sin(a)) + abs(h / 2 * math.cos(a))
    assert cx - half_w >= 4 and cx + half_w <= CARD_W - 4, "the stamp is wider than the card"
    assert cy + half_h <= CARD_H - 1, "the stamp hangs off the card"
    t0, t1 = T["stamp"]
    cls = TL({"transform": [(0, tf(s=1.8)), (t0, tf(s=1.8), "in"), (t1, tf(s=0.96)), (t1 + 0.08, tf())],
              "opacity": [(0, 0), (t0, 0), (t0 + 0.06, 1)]},
             base=FILL_BOX, seam=False)
    return (f'<g transform="translate({num(cx)},{cy}) rotate({angle})" opacity=".85"><g class="{cls}">'
            f'<g mask="url(#grunge)"><rect x="{num(-w / 2)}" y="{-h / 2}" width="{num(w)}" height="{h}" rx="5" '
            f'fill="none" stroke="{GREEN}" stroke-width="2.2"/>'
            + text(0, 5.2, label, "stamp", anchor="middle") + "</g></g></g>")


def check_and_confetti() -> str:
    cx, cy = CARD_W - 8, 4
    t0 = T["check"]
    cls = TL({"transform": pop(t0, 1.2), "opacity": [(0, 0), (t0, 0), (t0 + 0.01, 1)]},
             base=FILL_BOX, seam=False)
    check = (f'<g transform="translate({cx},{cy})"><g class="{cls}"><circle r="10" fill="{GREEN}"/>'
             f'<path d="M-4.6 .3L-1.3 3.5L4.7-3.3" fill="none" stroke="#0a0f1f" stroke-width="2.2" '
             f'stroke-linecap="round" stroke-linejoin="round"/></g></g>')
    rnd = random.Random(7)
    life, gravity, n = 1.2, 200, 14
    pieces = []
    for i in range(n):
        # a fan from straight up to the left: the card sits at the right edge
        angle = math.radians(-80 - 95 * i / (n - 1) - rnd.uniform(0, 8))
        speed = rnd.uniform(110, 165)
        vx, vy = speed * math.cos(angle), speed * math.sin(angle)
        spin = rnd.choice((-1, 1)) * rnd.uniform(360, 720)
        start = t0 + 0.05 + rnd.uniform(0, 0.05)
        track = [(0, tf()), (start, tf())]
        for k in range(1, 7):
            tau = life * k / 6
            x, y = vx * tau, vy * tau + gravity * tau * tau / 2
            track.append((start + tau, tf(x, y, spin * tau)))
            if k < 6:  # still visible: must stay inside the header
                ax, ay = COL_X[4] + cx + x, CARD_Y + cy + y
                assert 6 <= ax <= W - 6 and 6 <= ay <= H - 6, f"confetti {i} leaves the header"
        cls_p = TL({"transform": track,
                    "opacity": [(0, 0), (start, 0), (start + 0.02, 1), (start + life * 0.55, 1), (start + life, 0)]},
                   base=f"{FILL_BOX};opacity:0", seam=False)
        color = STAGE_COLORS[i % len(STAGE_COLORS)]
        shape = (f'<rect class="{cls_p}" x="-2" y="-4" width="4" height="8" rx=".6" fill="{color}"/>' if i % 2 == 0
                 else f'<circle class="{cls_p}" r="2.5" fill="{color}"/>')
        pieces.append(f'<g transform="translate({cx},{cy})">{shape}</g>')
    return check + "".join(pieces)


def pin() -> str:
    """Stage 5: a pushpin drops onto the top of the card."""
    t0, t1 = T["pin"]
    cls = TL({"transform": [(0, tf(y=-70)), (t0, tf(y=-70), "in"), (t1, tf(), "out"),
                            (t1 + 0.1, tf(y=-5), "in"), (t1 + 0.2, tf())],
              "opacity": [(0, 0), (t0, 0), (t0 + 0.1, 1)]},
             base="opacity:1", seam=False)
    return (f'<g transform="translate({CARD_W // 2},0)"><g class="{cls}">'
            f'<ellipse cx="5" cy="7.5" rx="6.5" ry="2.6" fill="#000" opacity=".45"/>'
            f'<path d="M0 0L1 6" stroke="#c9d1e0" stroke-width="1.6" stroke-linecap="round"/>'
            f'<circle cy="-6" r="7" fill="{PIN_RED}" stroke="#a8323a"/>'
            f'<ellipse cx="-2.4" cy="-8.6" rx="2.4" ry="1.5" fill="#fff" opacity=".75"/></g></g>')


def card(fold: str) -> str:
    rot = [(0, "rotate(-4deg)"), (MOVES[0][0], "rotate(-4deg)", "io"), (MOVES[0][1], "rotate(0deg)")]
    for k, d in enumerate((0, -1.5, 1.5, -1.5, 1.5, -1, 1, 0)):
        rot.append((T["shake"] + 0.08 * k, f"rotate({num(d)}deg)"))
    for k, d in enumerate((0, 1.2, -0.9, 0.5, 0)):
        rot.append((T["stamp"][1] + 0.06 * k, f"rotate({num(d)}deg)"))
    land, pin_hit = T["land"], T["pin"][1]
    dy = [(0, tf(y=-380), "in"), (land, tf(), "out"), (land + 0.12, tf(y=-12), "in"),
          (land + 0.22, tf(), "out"), (land + 0.25, tf(y=-3), "in"), (0.7, tf()),
          (pin_hit, tf(), "out"), (pin_hit + 0.06, tf(y=2), "out"), (pin_hit + 0.22, tf())]
    mv = [(0, tf())]
    for i, (a, b) in enumerate(MOVES):
        mv += [(a, tf(STEP * i), "io"), (b, tf(STEP * (i + 1)))]
    # The card is invisible across the loop seam (fully faded out, then parked
    # above the frame), which is what lets its inner tracks restart with seam=False.
    life = TL({"opacity": [(0, 0), (0.05, 1), (T["fade"][0], 1), (T["fade"][1], 0)]}, base="opacity:1")
    mv_cls = TL({"transform": mv}, base=f"transform:{tf(STEP * 4)}", seam=False)
    dy_cls = TL({"transform": dy}, base=f"transform:{tf()}", seam=False)
    rot_cls = TL({"transform": rot}, base=f"{FILL_BOX};transform:rotate(0deg)", seam=False)
    inner = [PIVOT, sheets(), body(fold), captions(), urgency_lines(), buttons(), typing(),
             edits(), coffee(), pills(), overdue(), stamp(), pin(), check_and_confetti()]
    return (f'<g transform="translate({COL_X[0]},{CARD_Y})"><g class="{life}"><g class="{mv_cls}">{dups()}'
            f'<g class="{dy_cls}"><g class="{rot_cls}">{"".join(inner)}</g></g></g></g></g>')


# ── assembly ────────────────────────────────────────────────────────────────
DOTS_KEYFRAMES = "@keyframes dot{0%,60%,100%{transform:translateY(0);opacity:.45}30%{transform:translateY(-3px);opacity:1}}"
DOTS_RULES = ".dot{animation:dot 1.2s ease-in-out infinite}.dot2{animation-delay:.15s}.dot3{animation-delay:.3s}"


def style_css() -> str:
    rules = []
    for cls, (family, weight, size, fill, ls) in STYLES.items():
        rule = f".{cls}{{font-family:'{family}',sans-serif;font-weight:{weight};font-size:{num(size)}px"
        rule += f";fill:{fill}" if fill else ""
        rule += f";letter-spacing:{num(ls)}px" if ls else ""
        rules.append(rule + "}")
    return "".join(rules)


def build() -> str:
    ensure_fonts()
    defs_svg, fold = defs()
    content = scenery() + header() + columns() + card(fold)
    border = (f'<rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="17.25" fill="none" '
              f'stroke="url(#border)" stroke-width="1.5"/>')
    css = (FONTS.css() + style_css() + "".join(TL.base) + "".join(TL.keyframes) + DOTS_KEYFRAMES
           + "@media (prefers-reduced-motion:no-preference){" + "".join(TL.rules) + DOTS_RULES + "}")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'role="img" aria-labelledby="title desc">\n'
           f'<title id="title">Mannco – {escape(ROLE)}</title>\n'
           f'<desc id="desc">An amoCRM pipeline: a deal card travels from Неразобранное to '
           f'Успешно реализовано and ends pinned, stamped ОПЛАЧЕНО and paid.</desc>\n'
           f'<style>{css}</style>\n{defs_svg}\n'
           f'<g clip-path="url(#frame)">{content}</g>{border}\n</svg>\n')
    verify(svg)
    return svg


def verify(svg: str) -> None:
    size = len(svg.encode())
    assert size <= MAX_BYTES, f"header.svg is {size / 1024:.1f} KB, limit {MAX_BYTES // 1024} KB"
    assert "<script" not in svg and not re.search(r"\son\w+=", svg), "no scripts"
    assert "<animate" not in svg and "<set" not in svg, "CSS animations only, no SMIL"
    assert re.findall(r"https?://[^\s\"')]+", svg) == ["http://www.w3.org/2000/svg"], "external reference"
    emoji = [c for chars in FONTS.used.values() for c in chars
             if ord(c) >= 0x1F000 or 0x2600 <= ord(c) <= 0x27BF or ord(c) == 0xFE0F]
    assert not emoji, f"emoji in text: {emoji}"
    for tag in re.findall(r"<[^>]+>", svg):
        if 'transform="' in tag:
            assert not re.search(r'class="[^"]*\ba\d+\b', tag), f"CSS transform on a transformed element: {tag}"


def main() -> None:
    svg = build()
    OUT.write_text(svg, encoding="utf-8", newline="\n")
    faces = ", ".join(f"{f} {w}: {len(c)} glyphs" for (f, w), c in sorted(FONTS.used.items()))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(svg.encode()) / 1024:.1f} KB; {faces}")


if __name__ == "__main__":
    main()
