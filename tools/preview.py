#!/usr/bin/env python3
"""Render review frames of assets/header.svg into tools/preview/.

    python tools/preview.py

Each frame pauses every animation at an exact moment of the loop instead of
waiting on the clock. Uses Playwright's Chromium; if it is not installed
(`playwright install chromium`), falls back to the local Chrome or Edge.
Needs: pip install playwright
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Error, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "header.svg"
OUT = ROOT / "tools" / "preview"
FRAMES = (0.4, 2, 3.6, 6, 11, 12.8, 16.8, 17.5, 19.8, 20.5, 21.2, 24, 28.4)
SEAM = (29.95, 0.0)  # both sides of the loop seam must look the same
README_WIDTH = 830
SEEK = "t => document.getAnimations().forEach(a => { a.pause(); a.currentTime = t * 1000; })"


def launch(p, args=()):
    for channel in (None, "chrome", "msedge"):
        try:
            return p.chromium.launch(channel=channel, args=list(args))
        except Error:
            continue
    raise SystemExit("no Chromium: run `playwright install chromium`")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    size = {"width": 1000, "height": 400}
    with sync_playwright() as p:
        browser = launch(p)

        page = browser.new_page(viewport=size)
        page.goto(SVG.as_uri())
        page.wait_for_load_state("load")
        svg = page.locator("svg")
        for t in FRAMES:
            page.evaluate(SEEK, t)
            svg.screenshot(path=OUT / f"t{t:04.1f}.png")
        for k, t in enumerate(SEAM):
            page.evaluate(SEEK, t)
            svg.screenshot(path=OUT / f"seam-{'ab'[k]}-{t:05.2f}.png")

        # Reduced motion drops every animation, leaving the static final frame.
        still = browser.new_page(viewport=size, reduced_motion="reduce")
        still.goto(SVG.as_uri())
        still.locator("svg").screenshot(path=OUT / "reduced-motion.png")

        browser.close()

        # As GitHub shows it: an <img> at README width on the dark and light
        # themes. An <img> cannot be paused and page-level emulation does not
        # reach inside it, so the whole browser is told to prefer reduced
        # motion and the image shows its static final frame.
        still_browser = launch(p, ["--force-prefers-reduced-motion"])
        for theme, bg in (("dark", "#0d1117"), ("light", "#ffffff")):
            html = OUT / f"img-{theme}.html"
            html.write_text(
                f'<body style="margin:0;padding:24px;background:{bg}">'
                f'<img src="{SVG.as_uri()}" width="{README_WIDTH}"></body>', encoding="utf-8")
            shot = still_browser.new_page(viewport={"width": README_WIDTH + 48, "height": 400})
            shot.goto(html.as_uri())
            shot.wait_for_load_state("load")
            shot.screenshot(path=OUT / f"img-{theme}.png", full_page=True)
        still_browser.close()
    print(f"frames in {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
