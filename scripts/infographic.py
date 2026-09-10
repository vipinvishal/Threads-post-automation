#!/usr/bin/env python3
"""Create and host the account's single approved handwritten poster.

Pipeline (called from generate_and_schedule.py):
    verified research + final post -> compact exact copy brief
                                   -> Gemini image generation, conditioned on
                                      the approved mascot/style reference
                                   -> imgbb public URL for Buffer

The older deterministic HTML renderers remain available for historical previews,
but production calls ``generate_handwritten_poster`` exclusively. This prevents
the model or weekday rotation from silently switching back to a carousel.
"""

import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from io import BytesIO

import requests
from google import genai
from google.genai import types
from PIL import Image

import infographic_templates as templates

# ── Paths ───────────────────────────────────────────────────────────────────────
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parent
_RENDERER   = _REPO_ROOT / "renderer"
_RENDER_PY  = _RENDERER / "render.py"

# ── Config ──────────────────────────────────────────────────────────────────────
# Handle shown on the infographic. This is the ONE canonical brand identity for the
# account — keep it in sync with FOLLOW_CTA in generate_and_schedule.py and never
# let a different brand name leak into generated content (system prompt, examples,
# fixtures). Change to your own brand without touching code, e.g. "@yourbrand".
INFOGRAPHIC_HANDLE = os.environ.get("INFOGRAPHIC_HANDLE", "@vipinailabs")
# Portfolio URL shown in the infographic footer. Rendered as TEXT inside the PNG
# (branding) — the clickable version is appended to the post body itself. Displayed
# without the scheme / trailing slash so it stays short and clean. Set "" to hide.
PORTFOLIO_URL      = (
    os.environ.get("PORTFOLIO_URL", "vipin-vishal.onrender.com")
    .strip().removeprefix("https://").removeprefix("http://").rstrip("/")
)
IMGBB_API_KEY      = os.environ.get("IMGBB_API_KEY", "")
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
GEMINI_IMAGE_SIZE  = os.environ.get("GEMINI_IMAGE_SIZE", "2K")
STYLE_REFERENCE    = pathlib.Path(
    os.environ.get(
        "INFOGRAPHIC_STYLE_REFERENCE",
        str(_REPO_ROOT / "assets" / "handwritten-poster-reference.png"),
    )
)

HANDWRITTEN_POSTER_TEMPLATE = "handwritten_poster"

_POSTER_COPY_SYSTEM = """You are a technical infographic editor. Convert a verified
Threads post into very short on-image copy. Never invent or round a number, benchmark,
price, product, result, or mechanism. Every factual claim must already appear in the
provided post or evidence. Return valid JSON only, with no Markdown or prose."""

_POSTER_COPY_PROMPT = """Create the exact copy for ONE vertical educational poster.

TOPIC: {topic}

FINAL VERIFIED THREADS POST:
{post}

SUPPORTING EVIDENCE:
{research}

The poster must tell the same single story as the post and be understandable in under
two seconds. Use short natural English. A card can be a step, option, or comparison;
do not imply sequence unless the source does. Use numbers only when they appear above.

Return exactly this JSON shape:
{{
  "headline_line1": "maximum 7 words",
  "headline_line2": "maximum 7 words",
  "context_line": "maximum 6 words",
  "cards": [
    {{"label": "maximum 3 words", "value": "maximum 4 words"}},
    {{"label": "maximum 3 words", "value": "maximum 4 words"}},
    {{"label": "maximum 3 words", "value": "maximum 4 words"}}
  ],
  "evidence_line1": "maximum 8 words",
  "evidence_line2": "maximum 8 words",
  "warning": "maximum 7 words",
  "takeaway_line1": "maximum 5 words",
  "takeaway_line2": "maximum 5 words"
}}
"""


def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # Be forgiving if the model adds prose around the object.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def _words(value, maximum: int) -> str:
    """Return plain, single-line poster copy bounded for phone readability."""
    cleaned = " ".join(str(value or "").replace("<", "").replace(">", "").split())
    return " ".join(cleaned.split()[:maximum]).strip()


def _coerce_poster_copy(data: dict) -> dict:
    cards = data.get("cards") or []
    if not isinstance(cards, list):
        cards = []
    normalized_cards = []
    for raw in cards[:3]:
        raw = raw if isinstance(raw, dict) else {}
        normalized_cards.append({
            "label": _words(raw.get("label"), 3),
            "value": _words(raw.get("value"), 4),
        })
    while len(normalized_cards) < 3:
        normalized_cards.append({"label": "", "value": ""})

    result = {
        "headline_line1": _words(data.get("headline_line1"), 7),
        "headline_line2": _words(data.get("headline_line2"), 7),
        "context_line": _words(data.get("context_line"), 6),
        "cards": normalized_cards,
        "evidence_line1": _words(data.get("evidence_line1"), 8),
        "evidence_line2": _words(data.get("evidence_line2"), 8),
        "warning": _words(data.get("warning"), 7),
        "takeaway_line1": _words(data.get("takeaway_line1"), 5),
        "takeaway_line2": _words(data.get("takeaway_line2"), 5),
    }
    required = (
        "headline_line1", "headline_line2", "context_line", "evidence_line1",
        "warning", "takeaway_line1", "takeaway_line2",
    )
    if any(not result[key] for key in required) or any(
        not card["label"] or not card["value"] for card in normalized_cards
    ):
        raise ValueError("poster copy contains empty required fields")
    return result


def generate_poster_copy(research: str, topic: str, post: str, generate_text_fn) -> dict:
    """Create the short, exact text block passed to the image model."""
    base_prompt = _POSTER_COPY_PROMPT.format(
        topic=topic,
        post=post[:900],
        research=research[:5000],
    )
    last_error = ""
    for attempt in range(1, 3):
        prompt = base_prompt
        if attempt > 1:
            prompt += f"\nPREVIOUS ATTEMPT FAILED: {last_error}\nReturn corrected JSON only."
        try:
            copy = _coerce_poster_copy(_parse_json(generate_text_fn(prompt, _POSTER_COPY_SYSTEM)))
            evidence = (post + "\n" + research).lower().replace(",", "")
            copy_text = " ".join([
                copy["headline_line1"], copy["headline_line2"], copy["context_line"],
                *(part for card in copy["cards"] for part in (card["label"], card["value"])),
                copy["evidence_line1"], copy["evidence_line2"], copy["warning"],
                copy["takeaway_line1"], copy["takeaway_line2"],
            ])
            numeric_tokens = re.findall(
                r"(?:₹|\$)?\d[\d,.]*(?:\.\d+)?(?:%|x|×|b|m|k|gb|mb|ms|s)?",
                copy_text.lower(),
            )
            unsupported = [
                token for token in numeric_tokens
                if token.replace(",", "") not in evidence
            ]
            if unsupported:
                raise ValueError(
                    "poster introduced numeric text absent from verified material: "
                    + ", ".join(dict.fromkeys(unsupported))
                )
            return copy
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  [Infographic] Poster-copy attempt {attempt} failed: {last_error}")
    raise RuntimeError(f"Poster copy generation failed: {last_error}")


def build_handwritten_poster_prompt(topic: str, copy: dict) -> str:
    """Build the production prompt while keeping all rendered wording explicit."""
    cards = copy["cards"]
    exact_lines = [
        copy["headline_line1"], copy["headline_line2"], copy["context_line"],
        cards[0]["label"], cards[0]["value"],
        cards[1]["label"], cards[1]["value"],
        cards[2]["label"], cards[2]["value"],
        copy["evidence_line1"], copy["evidence_line2"], copy["warning"],
        copy["takeaway_line1"], copy["takeaway_line2"], INFOGRAPHIC_HANDLE,
    ]
    quoted = "\n".join(f'"{line}"' for line in exact_lines)
    return f"""Use case: infographic-diagram
Asset type: one vertical 9:16 Threads infographic
Input image role: visual identity reference. Preserve its warm ivory paper, thick casual
black marker handwriting, rounded imperfect lettering, highlighter swashes, hand-drawn
arrows, sparse pastel technical cards, generous whitespace, and the same recurring cute
blue bird mascot with oversized black glasses, orange beak, orange feet, and thoughtful
pose at bottom-left. Do not copy any factual topic text from the reference.

Primary request: explain {topic} using the exact poster copy below.
Composition: oversized two-line curiosity hook at top; short context line; exactly three
pastel icon cards across the center with hand-drawn arrows; two-line evidence note; one
pink highlighted warning; mascot bottom-left; bold black paint-stroke takeaway bottom-right.
Use simple, topic-appropriate hand-drawn technical icons in the cards.

Text (verbatim): render every following line exactly once and add no other text:
{quoted}

Constraints: immediately readable on a phone; exact English spelling and punctuation;
high contrast; original technical illustration; no Instagram/Threads UI; no reactions,
music labels, profile overlays, hashtags, logos, or watermarks beyond {INFOGRAPHIC_HANDLE};
the mascot is a full-bodied original cartoon character, never a social-network logo.
"""


def _response_parts(response):
    parts = getattr(response, "parts", None)
    if parts:
        return parts
    candidates = getattr(response, "candidates", None) or []
    if candidates and getattr(candidates[0], "content", None):
        return getattr(candidates[0].content, "parts", None) or []
    return []


def generate_handwritten_poster(research: str, topic: str, post: str,
                                generate_text_fn, out_path: str) -> str:
    """Generate the only production visual, conditioned on the approved reference."""
    if not STYLE_REFERENCE.is_file():
        raise RuntimeError(f"Poster style reference is missing: {STYLE_REFERENCE}")

    api_keys = list(dict.fromkeys(filter(None, (
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GEMINI_API_KEY_2"),
    ))))
    if not api_keys:
        raise RuntimeError("GEMINI_API_KEY is required for handwritten poster generation")

    copy = generate_poster_copy(research, topic, post, generate_text_fn)
    prompt = build_handwritten_poster_prompt(topic, copy)
    reference = Image.open(STYLE_REFERENCE).convert("RGB")
    last_error = ""

    for key_index, api_key in enumerate(api_keys, 1):
        try:
            print(
                f"  [Infographic] Generating {HANDWRITTEN_POSTER_TEMPLATE} with "
                f"{GEMINI_IMAGE_MODEL} (key {key_index}/{len(api_keys)})..."
            )
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=GEMINI_IMAGE_MODEL,
                contents=[prompt, reference],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="9:16",
                        image_size=GEMINI_IMAGE_SIZE,
                    ),
                ),
            )
            image_bytes = next(
                (part.inline_data.data for part in _response_parts(response)
                 if getattr(part, "inline_data", None) and part.inline_data.data),
                None,
            )
            if not image_bytes:
                raise RuntimeError("image model returned no image bytes")

            output = pathlib.Path(out_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            generated = Image.open(BytesIO(image_bytes)).convert("RGB")
            width, height = generated.size
            if height <= width or width < 700 or height < 1200:
                raise RuntimeError(f"unexpected poster dimensions: {width}x{height}")
            generated.save(output, format="PNG", optimize=True)
            print(f"  [Infographic] Generated -> {output} ({width}x{height})")
            return str(output)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  [Infographic] Image key {key_index} failed: {last_error}")

    raise RuntimeError(f"Handwritten poster generation failed: {last_error}")


def generate_infographic_content(research: str, topic: str, generate_text_fn,
                                 post: str = "", image_template: str = "three_stage_flow",
                                 hook_style: str = "") -> dict:
    """Build validated infographic content JSON for the given image_template,
    reusing the text-gen chain.

    generate_text_fn(prompt, system) -> str   (the Gemini/Euron chain from main)

    `post` aligns the image to the post: same core solution/concept, so the
    infographic and the text tell one story. `hook_style` (one of
    generate_and_schedule.py's HOOK_STYLES keys) makes the image mirror the same
    scroll-stopping energy as the post's opening line — see
    infographic_templates.HOOK_STYLE_VISUAL_FRAMING.
    """
    if image_template not in templates.TEMPLATE_SPECS:
        raise ValueError(f"Unknown image_template '{image_template}' — must be one of {list(templates.TEMPLATE_SPECS)}")
    spec = templates.TEMPLATE_SPECS[image_template]
    prompt = templates.build_prompt(image_template, topic, research, post, hook_style)

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


def _render_one(content: dict, out_path: str) -> str:
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
    return out_path


def render_infographic(content: dict, out_path: str):
    """Render one infographic or a five-card carousel via Playwright."""
    print("  [Infographic] Rendering PNG with Playwright...")
    if content.get("_template_name") != "educational_carousel":
        result = _render_one(content, out_path)
        print(f"  [Infographic] Rendered -> {result}")
        return result

    output = pathlib.Path(out_path)
    rendered = []
    for index, slide in enumerate(content.get("slides", []), 1):
        card = dict(slide)
        card.update({
            "_template_name": "educational_carousel",
            "handle": content.get("handle", INFOGRAPHIC_HANDLE),
            "portfolio": content.get("portfolio", PORTFOLIO_URL),
            "source_note": content.get("source_note", ""),
        })
        card_path = str(output.with_name(f"{output.stem}-{index:02d}{output.suffix}"))
        rendered.append(_render_one(card, card_path))
    if len(rendered) != 5:
        raise RuntimeError(f"Carousel renderer expected 5 slides, produced {len(rendered)}")
    print(f"  [Infographic] Rendered carousel -> {', '.join(rendered)}")
    return rendered


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
