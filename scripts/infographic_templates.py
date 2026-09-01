#!/usr/bin/env python3
"""infographic_templates.py — one content schema + LLM prompt + coercion
function per image template.

scripts/infographic.py is a thin dispatcher: it looks up a template's spec
here by name, runs the shared generate -> parse -> coerce -> retry-once flow,
then stamps the result with "_template_name" so renderer/render.py knows
which .html.j2 file to render. Each template's Jinja variables (see
renderer/templates/<name>.html.j2) are exactly this spec's REQUIRED_KEYS.
"""

import re

# Icons the renderer ships with (renderer/icons.py). Shared across every
# template that places an icon (stages, timeline steps).
ICON_NAMES = [
    "cloud", "copies", "database", "file", "gear",
    "key", "laptop", "lock", "network", "search", "upload",
]


def _post_alignment_block(post: str) -> str:
    """Shared "align the image to the post" block, reused by every template."""
    post = (post or "").strip()
    if not post:
        return ""
    return (
        "\nTHE POST THIS IMAGE ACCOMPANIES — build the image around the SAME "
        "core idea/number it centers on, so image and text tell one story:\n"
        f"\"\"\"\n{post[:900]}\n\"\"\"\n"
    )


def _clean_icon(name, fallback="file"):
    return name if name in ICON_NAMES else fallback


# ══════════════════════════════════════════════════════════════════════════
# three_stage_flow — a genuinely sequential "how it works" mechanism
# ══════════════════════════════════════════════════════════════════════════

_TSF_SYSTEM = """You are an engineer who tests AI systems hands-on and designs single-image
explainer infographics of what you figured out — clear and accurate, never a know-it-all
lecture. You take research on AI systems, infrastructure, or model architecture and reframe
it into ONE "how it works" concept explained in exactly 3 visual stages. Cover AI, LLMs,
model architecture, training, inference, GPUs/infrastructure, RAG, agents, and related
systems topics. Be precise and technically accurate — never invent numbers or mechanisms,
never use hype words. Write ALL text in ENGLISH ONLY. You return valid JSON only: no markdown, no prose."""

_TSF_USER_TEMPLATE = """CONTEXT on this topic (reference material — use only what's accurate, never invent):
Topic: "{topic}"

SOURCES / CONTEXT:
{research}
{alignment}
---

TASK
Reframe this into ONE teachable AI concept that fits a 3-stage "how it works"
infographic. Only use this shape if the concept is genuinely SEQUENTIAL (step 1
causes step 2 causes step 3) — prefer the underlying mechanism over the headline
(e.g. a story about a new agent framework -> "How an AI Agent Decides Its Next Action").

HARD RULES — SKIMMABLE IN UNDER 2 SECONDS, so every field is short:
- Topic MUST be about AI / ML systems: models, architecture, training, inference,
  GPUs/infrastructure, RAG, agents, or related systems concepts. Be technically accurate.
- EXACTLY 3 stages and EXACTLY 3 explainers.
- Every value concrete and specific — no filler like "AI is powerful".
- stage.title <= 14 characters, 1-2 words. stage.subtitle <= 20 characters, one short phrase.
- stage.icon MUST be one of: {icons}
- arrow_note is a tiny 1-2 word label; the LAST stage's arrow_note MUST be "".
- explainer.body is ONE short sentence, <=70 characters — not two sentences. May use
  <span class='k1'>..</span>, <span class='k2'>..</span>, <span class='k3'>..</span>
  to highlight a key term, and <b>..</b> for bold.
- quote_main is the single most surprising number/fact from the research — this is the
  large headline-style stat banner, not a body sentence. May use <span class='n'>NUMBER</span>
  and <span class='h'>highlight</span>.
- quote_sub <=50 characters, one short supporting line.
- terminal_cmd is a short, real-looking shell/CLI line, <= 18 chars, ideally
  one token (e.g. "agent.run()", "rag.query()"). No long arguments.
- sticky1 and sticky2 are <= 5 words each.

Return a single JSON object with EXACTLY these keys:
{{
  "headline_line1_pre": "text before the highlighted word, e.g. 'How '",
  "headline_line1_hl": "the ONE highlighted word, e.g. 'RAG'",
  "headline_line1_post": "text after it on line 1 (may be empty)",
  "headline_line2": "the second headline line (blue)",
  "sub_pre": "short lead, e.g. 'A Query Travels Through'",
  "sub_num": "3",
  "sub_post": "e.g. 'Stages'",
  "stages": [
    {{"title": "<=14 chars", "subtitle": "<=20 chars", "icon": "one of the icons", "arrow_note": "1-2 words"}},
    {{"title": "<=14 chars", "subtitle": "<=20 chars", "icon": "one of the icons", "arrow_note": "1-2 words"}},
    {{"title": "<=14 chars", "subtitle": "<=20 chars", "icon": "one of the icons", "arrow_note": ""}}
  ],
  "explainers": [
    {{"tag": "short heading", "body": "ONE short sentence <=70 chars, may use <span class='k1'> and <b>"}},
    {{"tag": "short heading", "body": "ONE short sentence <=70 chars, may use <span class='k2'> and <b>"}},
    {{"tag": "short heading", "body": "ONE short sentence <=70 chars, may use <span class='k3'> and <b>"}}
  ],
  "sticky1": "short aha note, use <b> for the key word",
  "terminal_cmd": "short CLI command",
  "sticky2": "short aha note, use <b> for the key word",
  "quote_main": "the single most surprising number/fact, use <span class='n'> for a number and <span class='h'> for highlight",
  "quote_sub": "<=50 chars, one supporting line"
}}"""

_TSF_REQUIRED_KEYS = [
    "headline_line1_pre", "headline_line1_hl", "headline_line1_post",
    "headline_line2", "sub_pre", "sub_num", "sub_post", "stages", "explainers",
    "sticky1", "terminal_cmd", "sticky2", "quote_main", "quote_sub",
]


def _coerce_three_stage_flow(data: dict) -> dict:
    stages = data.get("stages") or []
    while len(stages) < 3:
        stages.append({"title": "", "subtitle": "", "icon": "file", "arrow_note": ""})
    stages = stages[:3]
    for i, st in enumerate(stages):
        st.setdefault("title", "")
        st.setdefault("subtitle", "")
        st["icon"] = _clean_icon(st.get("icon", "file"))
        st["arrow_note"] = "" if i == 2 else st.get("arrow_note", "")
    data["stages"] = stages

    exps = data.get("explainers") or []
    while len(exps) < 3:
        exps.append({"tag": "", "body": ""})
    for ex in exps:
        ex.setdefault("tag", "")
        ex.setdefault("body", "")
    data["explainers"] = exps[:3]

    data["sub_num"] = str(data.get("sub_num", "3"))
    for key in _TSF_REQUIRED_KEYS:
        data.setdefault(key, "")
    return data


# ══════════════════════════════════════════════════════════════════════════
# single_stat_hero — one dominant, real number (hot_take / india_cost)
# ══════════════════════════════════════════════════════════════════════════

_SSH_SYSTEM = """You are an engineer who tests AI tools hands-on and reports real costs and real
results, especially framed for Indian developers deciding whether something is worth their
time or money. You design a single-image infographic built around ONE dominant, real number
(a cost, a latency, a token price, a percentage). Never invent or round a number beyond what
the source material says. No hype words. Write ALL text in ENGLISH ONLY. You return valid
JSON only: no markdown, no prose."""

_SSH_USER_TEMPLATE = """CONTEXT on this topic (reference material — use only what's accurate, never invent):
Topic: "{topic}"

SOURCES / CONTEXT:
{research}
{alignment}
---

TASK
Pull out the ONE real, specific number this post/research centers on (a ₹ cost, a
latency figure, a token price, a percentage) and build a single-stat hero infographic
around it. The number must come directly from the post or research — never invent or
estimate one.

HARD RULES — SKIMMABLE IN UNDER 2 SECONDS:
- stat_number is short: <=14 characters (e.g. "₹1,850", "40ms", "$0.002").
- eyebrow is a short label, <=24 characters, all caps reads well (e.g. "REAL COST CHECK").
- stat_label <=32 characters, explains what the number is per (e.g. "/month, RunPod 7B model").
- context_lines: exactly 1 short sentence, <=80 characters. May use <span class="n">..</span>
  for a secondary number and <span class="h">..</span> to highlight a phrase.
- source_note: a tiny factual footnote, <=50 characters, or "" if nothing to cite.

Return a single JSON object with EXACTLY these keys:
{{
  "eyebrow": "<=24 chars",
  "stat_number": "<=14 chars",
  "stat_label": "<=32 chars",
  "context_lines": ["exactly 1 short sentence, <=80 chars"],
  "source_note": "<=50 chars or empty string"
}}"""

_SSH_REQUIRED_KEYS = ["eyebrow", "stat_number", "stat_label", "context_lines", "source_note"]


def _coerce_single_stat_hero(data: dict) -> dict:
    lines = data.get("context_lines") or []
    if isinstance(lines, str):
        lines = [lines]
    data["context_lines"] = [str(l) for l in lines if str(l).strip()][:1] or [""]
    for key in _SSH_REQUIRED_KEYS:
        data.setdefault(key, "")
    return data


# ══════════════════════════════════════════════════════════════════════════
# before_after — a trade-off or comparison, not a sequence
# ══════════════════════════════════════════════════════════════════════════

_BA_SYSTEM = """You are an engineer who tests AI systems hands-on and designs single-image
explainer infographics. You take a trade-off or comparison (with vs without a technique,
old vs new approach) and build a clean before/after infographic — two short, honest lists
of concrete differences. Never invent a mechanism or number. No hype words. Write ALL text
in ENGLISH ONLY. You return valid JSON only: no markdown, no prose."""

_BA_USER_TEMPLATE = """CONTEXT on this topic (reference material — use only what's accurate, never invent):
Topic: "{topic}"

SOURCES / CONTEXT:
{research}
{alignment}
---

TASK
Frame this as a BEFORE/AFTER comparison — without the technique/approach vs with it.
Only use this shape for a genuine trade-off or comparison (not a sequential process).

HARD RULES — SKIMMABLE IN UNDER 2 SECONDS:
- headline_hl is the ONE highlighted word/phrase in the headline (e.g. the technique name).
- hero_stat: IF the research/post centers on one standout number (a cost, a speedup, a
  percentage), put it here as a large headline-style badge, <=10 characters (e.g. "10x",
  "₹1,200", "40ms") — this is the single most surprising number, not buried in a bullet.
  hero_stat_label: <=24 chars, what the number means (e.g. "faster than no cache"). Leave
  both "" if there's no single standout number for this topic — never invent one.
- before_label / after_label: <=16 characters each (e.g. "No KV cache" / "With KV cache").
- before_points / after_points: exactly 2 short, concrete lines each, <=32 characters, no filler.
- takeaway: one sharp closing sentence, <=70 characters, may use <b>..</b> for emphasis.

Return a single JSON object with EXACTLY these keys:
{{
  "headline_pre": "text before the highlighted word",
  "headline_hl": "the ONE highlighted word/phrase",
  "headline_post": "text after it (may be empty)",
  "hero_stat": "<=10 chars, or empty string if no standout number",
  "hero_stat_label": "<=24 chars, or empty string",
  "before_label": "<=16 chars",
  "before_points": ["exactly 2 short lines, <=32 chars each"],
  "after_label": "<=16 chars",
  "after_points": ["exactly 2 short lines, <=32 chars each"],
  "takeaway": "<=70 chars, may use <b>"
}}"""

_BA_REQUIRED_KEYS = [
    "headline_pre", "headline_hl", "headline_post", "hero_stat", "hero_stat_label",
    "before_label", "before_points", "after_label", "after_points", "takeaway",
]


def _coerce_before_after(data: dict) -> dict:
    for key in ("before_points", "after_points"):
        pts = data.get(key) or []
        if isinstance(pts, str):
            pts = [pts]
        data[key] = [str(p) for p in pts if str(p).strip()][:2]
    for key in _BA_REQUIRED_KEYS:
        data.setdefault(key, "")
    return data


# ══════════════════════════════════════════════════════════════════════════
# annotated_screenshot — a stylized terminal/log with callouts (build_log)
# ══════════════════════════════════════════════════════════════════════════

_AS_SYSTEM = """You are an engineer who builds and ships AI systems and writes honest build-log
posts about what broke or surprised you. You design a single-image infographic styled as a
terminal/log window with a couple of lines from a real-feeling incident, annotated with 1-2
callouts explaining what actually went wrong. Never invent a fake vulnerability or a made-up
error unrelated to the story — ground it in the post's actual story. Write ALL text in
ENGLISH ONLY. You return valid JSON only: no markdown, no prose."""

_AS_USER_TEMPLATE = """CONTEXT on this topic (reference material — use only what's accurate, never invent):
Topic: "{topic}"

SOURCES / CONTEXT:
{research}
{alignment}
---

TASK
Recreate the moment this build-log post describes as a short terminal/log window (2-5
lines) with 2-3 hand-drawn callouts pointing at the specific line(s) that show what broke.

HARD RULES — SKIMMABLE IN UNDER 2 SECONDS:
- terminal_title: a short file/path-like label, <=28 characters (e.g. "pipeline/ingest.py"). Never invent a project or company name.
- terminal_lines: 2-5 short lines, each <=36 characters, monospace-appropriate (timestamps,
  log levels, short commands/output are good).
- callouts: 2-3 items, each {{"target_line": <0-based index into terminal_lines>, "label": "<=40 chars"}}.
  target_line MUST be a valid index into terminal_lines.
- lesson: one honest closing sentence, <=70 characters, may use <b>..</b> for emphasis.

Return a single JSON object with EXACTLY these keys:
{{
  "headline_pre": "text before the highlighted word",
  "headline_hl": "the ONE highlighted word/phrase",
  "headline_post": "text after it (may be empty)",
  "terminal_title": "<=28 chars",
  "terminal_lines": ["2-5 short lines, <=36 chars each"],
  "callouts": [{{"target_line": 0, "label": "<=40 chars"}}],
  "lesson": "<=70 chars, may use <b>"
}}"""

_AS_REQUIRED_KEYS = [
    "headline_pre", "headline_hl", "headline_post",
    "terminal_title", "terminal_lines", "callouts", "lesson",
]


def _coerce_annotated_screenshot(data: dict) -> dict:
    lines = data.get("terminal_lines") or []
    if isinstance(lines, str):
        lines = [lines]
    lines = [str(l) for l in lines if str(l).strip()][:5]
    data["terminal_lines"] = lines

    callouts = data.get("callouts") or []
    cleaned = []
    for c in callouts:
        if not isinstance(c, dict):
            continue
        try:
            idx = int(c.get("target_line"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(lines) and str(c.get("label", "")).strip():
            cleaned.append({"target_line": idx, "label": str(c["label"])})
    data["callouts"] = cleaned[:3]
    for key in _AS_REQUIRED_KEYS:
        data.setdefault(key, "")
    return data


# ══════════════════════════════════════════════════════════════════════════
# timeline — a genuine sequence longer than 3 steps
# ══════════════════════════════════════════════════════════════════════════

_TL_SYSTEM = """You are an engineer who tests AI systems hands-on and designs single-image
explainer infographics. You take a genuinely sequential process (more than 3 real steps)
and lay it out as a timeline of ordered milestones. Only use this shape for a real
chronological/ordered sequence — never invent a mechanism or number. Write ALL text in
ENGLISH ONLY. You return valid JSON only: no markdown, no prose."""

_TL_USER_TEMPLATE = """CONTEXT on this topic (reference material — use only what's accurate, never invent):
Topic: "{topic}"

SOURCES / CONTEXT:
{research}
{alignment}
---

TASK
Lay this out as an ordered timeline of 3-5 real milestones/steps.

HARD RULES — SKIMMABLE IN UNDER 2 SECONDS:
- steps: 3-5 items, each {{"label": "<=16 chars", "detail": "<=26 chars", "icon": "one of the icons"}}.
- icon MUST be one of: {icons}
- closing_thought: one sharp closing sentence, <=70 characters, may use <b>..</b>.

Return a single JSON object with EXACTLY these keys:
{{
  "headline_pre": "text before the highlighted word",
  "headline_hl": "the ONE highlighted word/phrase",
  "headline_post": "text after it (may be empty)",
  "steps": [
    {{"label": "<=16 chars", "detail": "<=26 chars", "icon": "one of the icons"}}
  ],
  "closing_thought": "<=70 chars, may use <b>"
}}"""

_TL_REQUIRED_KEYS = ["headline_pre", "headline_hl", "headline_post", "steps", "closing_thought"]


def _coerce_timeline(data: dict) -> dict:
    steps = data.get("steps") or []
    cleaned = []
    for st in steps:
        if not isinstance(st, dict):
            continue
        cleaned.append({
            "label": str(st.get("label", "")),
            "detail": str(st.get("detail", "")),
            "icon": _clean_icon(st.get("icon", "file")),
        })
    data["steps"] = cleaned[:5]
    for key in _TL_REQUIRED_KEYS:
        data.setdefault(key, "")
    return data


# ══════════════════════════════════════════════════════════════════════════
# Registry — keyed by image_template name (matches renderer/render.py's
# TEMPLATE_FILES and the "image_template" values in the post-generation JSON)
# ══════════════════════════════════════════════════════════════════════════

TEMPLATE_SPECS = {
    "three_stage_flow": {
        "system": _TSF_SYSTEM,
        "user_template": _TSF_USER_TEMPLATE,
        "required_keys": _TSF_REQUIRED_KEYS,
        "coerce": _coerce_three_stage_flow,
        "needs_icons": True,
    },
    "single_stat_hero": {
        "system": _SSH_SYSTEM,
        "user_template": _SSH_USER_TEMPLATE,
        "required_keys": _SSH_REQUIRED_KEYS,
        "coerce": _coerce_single_stat_hero,
        "needs_icons": False,
    },
    "before_after": {
        "system": _BA_SYSTEM,
        "user_template": _BA_USER_TEMPLATE,
        "required_keys": _BA_REQUIRED_KEYS,
        "coerce": _coerce_before_after,
        "needs_icons": False,
    },
    "annotated_screenshot": {
        "system": _AS_SYSTEM,
        "user_template": _AS_USER_TEMPLATE,
        "required_keys": _AS_REQUIRED_KEYS,
        "coerce": _coerce_annotated_screenshot,
        "needs_icons": False,
    },
    "timeline": {
        "system": _TL_SYSTEM,
        "user_template": _TL_USER_TEMPLATE,
        "required_keys": _TL_REQUIRED_KEYS,
        "coerce": _coerce_timeline,
        "needs_icons": True,
    },
}


def build_prompt(template_name: str, topic: str, research: str, post: str) -> str:
    """Fill in a template's user prompt with topic/research/post-alignment."""
    spec = TEMPLATE_SPECS[template_name]
    kwargs = dict(
        topic=topic,
        research=(research or "").strip()[:5500] or topic,
        alignment=_post_alignment_block(post),
    )
    if spec["needs_icons"]:
        kwargs["icons"] = ", ".join(ICON_NAMES)
    return spec["user_template"].format(**kwargs)
