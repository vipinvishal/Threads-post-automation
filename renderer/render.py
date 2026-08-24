#!/usr/bin/env python3
"""Render an infographic PNG from a content JSON file.

Usage:
    python render.py data/sample_content.json output/infographic.png

The content dict picks which template to render via an internal
"_template_name" key (set by scripts/infographic_templates.py, defaults to
"three_stage_flow" for backward compatibility). The auto-fit pass measures
every ".autofit" element in the real browser and shrinks its font-size until
it fits its box, so variable-length LLM output never overflows — each
template just tags its variable-length elements with class="autofit" plus
data-min-size (and optionally data-max-height) instead of the renderer
hardcoding per-template CSS selectors.
"""
import sys, json, pathlib
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
from icons import get_icon

ROOT = pathlib.Path(__file__).parent
TEMPLATES_DIR = ROOT / "templates"
PORTRAIT_B64 = (ROOT / "data" / "portrait_b64.txt").read_text().strip()
FONT_CSS = (ROOT / "fonts" / "embedded_fonts.css").read_text()

ENV = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

TEMPLATE_FILES = {
    "three_stage_flow": "three_stage_flow.html.j2",
    "single_stat_hero": "single_stat_hero.html.j2",
    "before_after": "before_after.html.j2",
    "annotated_screenshot": "annotated_screenshot.html.j2",
    "timeline": "timeline.html.j2",
}


def _resolve_icons(node):
    """Recursively swap any dict's "icon" name field for its raw SVG markup.

    Icon resolution happens in Python (not Jinja) so every template can just
    do `{{ x.icon | safe }}` regardless of where in the content tree it sits.
    """
    if isinstance(node, dict):
        if isinstance(node.get("icon"), str):
            node["icon"] = get_icon(node["icon"])
        for value in node.values():
            _resolve_icons(value)
    elif isinstance(node, list):
        for value in node:
            _resolve_icons(value)


def build_html(content):
    template_name = content.pop("_template_name", "three_stage_flow")
    template_file = TEMPLATE_FILES.get(template_name, TEMPLATE_FILES["three_stage_flow"])
    tpl = ENV.get_template(template_file)
    _resolve_icons(content)
    content["portrait_b64"] = PORTRAIT_B64
    content["font_css"] = FONT_CSS
    return tpl.render(**content)


def autofit(page):
    """Shrink any `.autofit` element that overflows its box. Runs in-browser.

    Each element opts in via class="autofit" plus data-min-size (px floor)
    and, optionally, data-max-height (px ceiling) — the same generic contract
    across all 5 templates instead of one hardcoded selector list per layout.
    """
    page.evaluate("""() => {
        document.querySelectorAll('.autofit').forEach(el => {
            const minSize = parseFloat(el.dataset.minSize || '10');
            const maxHeight = el.dataset.maxHeight ? parseFloat(el.dataset.maxHeight) : null;
            const box = el.parentElement;
            let size = parseFloat(getComputedStyle(el).fontSize);
            while (size > minSize && (
                    el.scrollWidth > box.clientWidth ||
                    (maxHeight !== null && el.scrollHeight > maxHeight)
                  )) {
                size -= 1;
                el.style.fontSize = size + 'px';
            }
        });
    }""")


def render(content_path, out_path):
    content = json.loads(pathlib.Path(content_path).read_text())
    html = build_html(content)
    tmp_html = ROOT / "output" / "_tmp.html"
    tmp_html.parent.mkdir(parents=True, exist_ok=True)   # gitignored dir; create on fresh checkouts (CI)
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp_html.write_text(html)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 1120},
                                device_scale_factor=2)   # 2x = crisp 1800px PNG
        page.goto(tmp_html.as_uri())
        page.wait_for_timeout(400)        # let fonts load
        autofit(page)
        page.wait_for_timeout(100)
        el = page.query_selector(".page")
        el.screenshot(path=str(out_path))
        browser.close()
    print(f"rendered -> {out_path}")


if __name__ == "__main__":
    cpath = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "sample_content.json")
    opath = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "output" / "infographic.png")
    render(cpath, opath)
