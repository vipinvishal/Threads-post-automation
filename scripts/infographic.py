#!/usr/bin/env python3
"""infographic.py — Turn the day's research into a branded hand-drawn-style
infographic PNG, then host it so Buffer can attach it to the Threads post.

Pipeline (called from generate_and_schedule.py):
    research brief  ->  content JSON (reuses the Gemini/Euron text chain,
                        schema/prompt/coercion picked by image_template name
                        from scripts/infographic_templates.py)
                    ->  renderer/render.py  (Playwright -> 1800px PNG)
                    ->  imgbb  (public URL for Buffer's assets[].image.url)

The renderer is the proven system from Auto_infographics_system (Jinja2
templates + embedded handwriting fonts + portrait). We only swap "email the
PNG" for "upload it and return a public URL".
"""

import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import requests

import infographic_templates as templates

# ── Paths ───────────────────────────────────────────────────────────────────────
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parent
_RENDERER   = _REPO_ROOT / "renderer"
_RENDER_PY  = _RENDERER / "render.py"

# ── Config ──────────────────────────────────────────────────────────────────────
# Handle shown on the infographic. Change to your brand without touching code,
# e.g. INFOGRAPHIC_HANDLE="@orbitailabs".
INFOGRAPHIC_HANDLE = os.environ.get("INFOGRAPHIC_HANDLE", "@vipinailabs")
# Portfolio URL shown in the infographic footer. Rendered as TEXT inside the PNG
# (branding) — the clickable version is appended to the post body itself. Displayed
# without the scheme / trailing slash so it stays short and clean. Set "" to hide.
PORTFOLIO_URL      = (
    os.environ.get("PORTFOLIO_URL", "vipin-vishal.onrender.com")
    .strip().removeprefix("https://").removeprefix("http://").rstrip("/")
)
IMGBB_API_KEY      = os.environ.get("IMGBB_API_KEY", "")


def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # Be forgiving if the model adds prose around the object.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def generate_infographic_content(research: str, topic: str, generate_text_fn,
                                 post: str = "", image_template: str = "three_stage_flow") -> dict:
    """Build validated infographic content JSON for the given image_template,
    reusing the text-gen chain.

    generate_text_fn(prompt, system) -> str   (the Gemini/Euron chain from main)

    `post` aligns the image to the post: same core solution/concept, so the
    infographic and the text tell one story.
    """
    if image_template not in templates.TEMPLATE_SPECS:
        raise ValueError(f"Unknown image_template '{image_template}' — must be one of {list(templates.TEMPLATE_SPECS)}")
    spec = templates.TEMPLATE_SPECS[image_template]
    prompt = templates.build_prompt(image_template, topic, research, post)

    last_err = ""
    for attempt in range(1, 3):  # one retry
        user = prompt if attempt == 1 else prompt + f"\n\nPREVIOUS ATTEMPT FAILED: {last_err}\nReturn corrected JSON only."
        raw = generate_text_fn(user, spec["system"])
        try:
            data = spec["coerce"](_parse_json(raw))
            data["handle"] = INFOGRAPHIC_HANDLE
            data["portfolio"] = PORTFOLIO_URL   # deterministic; never model-generated
            data["_template_name"] = image_template
            print(f"  [Infographic] Content ready — template: {image_template}")
            return data
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            print(f"  [Infographic] Content parse failed (attempt {attempt}): {last_err}")
    raise RuntimeError(f"Infographic content generation failed: {last_err}")


def render_infographic(content: dict, out_path: str) -> str:
    """Render the content JSON to a PNG via renderer/render.py (Playwright)."""
    print("  [Infographic] Rendering PNG with Playwright...")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(content, fh)
        content_path = fh.name
    try:
        subprocess.run(
            [sys.executable, str(_RENDER_PY), content_path, out_path],
            check=True, cwd=str(_RENDERER),
        )
    finally:
        os.unlink(content_path)
    print(f"  [Infographic] Rendered -> {out_path}")
    return out_path


def upload_to_imgbb(png_path: str) -> str:
    """Upload the PNG to imgbb and return a public direct URL for Buffer."""
    if not IMGBB_API_KEY:
        raise RuntimeError("IMGBB_API_KEY not set — cannot host the infographic.")
    print("  [Infographic] Uploading to imgbb...")
    with open(png_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        params={"key": IMGBB_API_KEY},
        data={"image": b64},
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"imgbb upload {resp.status_code}: {resp.text[:300]}")
    url = resp.json()["data"]["url"]
    print(f"  [Infographic] Hosted at: {url}")
    return url
