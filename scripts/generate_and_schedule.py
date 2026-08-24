#!/usr/bin/env python3
"""
Threads Post Agent
Pipeline: Exa (research) → Gemini (generate JSON post) → quality gates → Buffer (schedule)

Run locally : python scripts/generate_and_schedule.py
GitHub Actions triggers this automatically 3x/day.
"""

import difflib
import json
import os
import random
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from exa_py import Exa
from google import genai
from google.genai import types
from dotenv import load_dotenv

import infographic

# ── Load env (local dev; GitHub Actions injects env vars directly) ────────────
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY")
GEMINI_API_KEY_2  = os.environ.get("GEMINI_API_KEY_2")
EURON_API_KEY     = os.environ.get("EURON_API_KEY")
EXA_API_KEY       = os.environ.get("EXA_API_KEY")
BUFFER_API_KEY    = os.environ.get("BUFFER_API_KEY")
BUFFER_CHANNEL_ID = os.environ.get("BUFFER_CHANNEL_ID")

GEMINI_MODEL           = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash", "gemini-2.0-flash-001"]
EURON_MODEL            = os.environ.get("EURON_MODEL", "gemini-2.5-flash")

# ── News freshness ──────────────────────────────────────────────────────────────
# Only pull articles published within this rolling window (hours). If the strict
# window returns nothing, we widen to NEWS_FALLBACK_HOURS so the run never produces
# an empty/garbage post.
NEWS_WINDOW_HOURS   = int(os.environ.get("NEWS_WINDOW_HOURS", "48"))
NEWS_FALLBACK_HOURS = int(os.environ.get("NEWS_FALLBACK_HOURS", "168"))  # 7 days

# ── Infographic image ────────────────────────────────────────────────────────────
# When on, each post gets a rendered infographic PNG attached (needs IMGBB_API_KEY
# to host it for Buffer). Set INCLUDE_INFOGRAPHIC=0 to fall back to text-only.
INCLUDE_INFOGRAPHIC = os.environ.get("INCLUDE_INFOGRAPHIC", "1") not in ("0", "false", "False", "")

# ── Humanize pass ─────────────────────────────────────────────────────────────────
# After Gemini writes the post, run a second pass that rewrites the phrasing so it
# reads like a specific person wrote it instead of an AI (strips "AI slop" phrasing,
# varies rhythm) without touching the meaning, numbers, or claims. Uses the same
# Gemini/Euron fallback chain as generation. Set HUMANIZE_POST=0 to skip it.
HUMANIZE_POST = os.environ.get("HUMANIZE_POST", "1") not in ("0", "false", "False", "")

# ── Follow CTA + portfolio link ─────────────────────────────────────────────────────
# Only appended when the post's gate-checked cta_included is True (roughly 1 in 6-8
# posts — see DAY_ROTATION + cta_cap_allows), never on every post. NOTE: the handle
# must be the REAL account handle (@vipinailabs) — Threads only renders a tappable
# mention when the handle resolves; a wrong one degrades to plain text.
FOLLOW_CTA = os.environ.get("FOLLOW_CTA", "follow @vipinailabs for daily dose of information")
PORTFOLIO_CTA  = os.environ.get("PORTFOLIO_CTA", "See what I've been building →")
_raw_portfolio = os.environ.get("PORTFOLIO_URL", "vipin-vishal.onrender.com").strip()
PORTFOLIO_LINK = (
    (_raw_portfolio if _raw_portfolio.startswith(("http://", "https://"))
     else f"https://{_raw_portfolio}")
    if _raw_portfolio else ""
)

# ── Topic tag ────────────────────────────────────────────────────────────────────
# Threads turns the FIRST hashtag in a post into a native topic tag (only one is
# allowed). Tags are constrained to the account's PINNED INTERESTS so Threads routes
# the post into the right topic feeds: AI, AgenticAI, CloudComputing. The model picks
# its own tag (see OUTPUT FORMAT in POST_SYSTEM_PROMPT); PINNED_TAGS validates it and
# TOPIC_TAG_RULES is the keyword-based fallback when the model's tag doesn't resolve.
PINNED_TAGS = {"ai": "#AI", "agenticai": "#AgenticAI", "cloudcomputing": "#CloudComputing"}
TOPIC_TAG_RULES = [
    (("agent", "agentic", "autonomous", "multi-agent", "tool use", "orchestrat"), "#AgenticAI"),
    (("cloud", "aws", "azure", "gcp", "kubernetes", "serverless", "infrastructure", "datacenter", "data center"), "#CloudComputing"),
]
DEFAULT_TOPIC_TAG = os.environ.get("DEFAULT_TOPIC_TAG", "#AI")


def pick_topic_tag(text: str) -> str:
    """Keyword-based fallback topic tag, used when the model's own tag doesn't
    resolve to one of the account's pinned interests."""
    lowered = text.lower()
    for keywords, tag in TOPIC_TAG_RULES:
        if any(k in lowered for k in keywords):
            return tag
    return DEFAULT_TOPIC_TAG


def validate_tag(model_tag: str, body: str, topic: str) -> str:
    """Normalize the model's own `tag` field against the 3 pinned interests;
    fall back to the keyword heuristic if it doesn't resolve."""
    key = re.sub(r"[^a-z]", "", str(model_tag or "").lower())
    if key in PINNED_TAGS:
        return PINNED_TAGS[key]
    return pick_topic_tag(body + " " + topic)


MAX_RETRIES        = 4
RETRY_BASE_SECONDS = 15

# ── Load topics config ────────────────────────────────────────────────────────
_script_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_script_dir, "topics.json"), "r") as f:
    _config = json.load(f)

NICHE         = _config["niche"]
FORMAT_TOPICS = _config["format_topics"]


def pick_topic_for_format(format_key: str) -> str:
    """Pick a random topic seed for this format's Exa search. quote_react has no
    list here — its "topic" is whatever claim gets sourced live (see
    research_claim_to_react_to)."""
    topics = FORMAT_TOPICS.get(format_key, {}).get("topics") or [format_key]
    return random.choice(topics)


# ══════════════════════════════════════════════════════════════════════════════
# WEEKLY FORMAT ROTATION  (pipeline-enforced — the model never picks its own format)
# ══════════════════════════════════════════════════════════════════════════════
# Day of week (IST) locks the format + the allowed image_template options for every
# run that day. Friday is the one CTA-eligible slot; the rolling cta_cap_allows()
# check (see below) still hard-caps the real ratio to ~1-in-8 even across 3 runs/day.
# Sunday is a reply-only day: no research/generation/posting happens at all.
DAY_ROTATION = {
    0: {"format": "mechanism_explainer", "image_templates": ["three_stage_flow", "before_after"], "cta_eligible": False},  # Mon
    1: {"format": "hot_take",            "image_templates": ["single_stat_hero"],                  "cta_eligible": False},  # Tue
    2: {"format": "build_log",           "image_templates": ["annotated_screenshot", "none"],      "cta_eligible": False},  # Wed
    3: {"format": "india_cost",          "image_templates": ["single_stat_hero"],                  "cta_eligible": False},  # Thu
    4: {"format": "mechanism_explainer", "image_templates": ["before_after", "timeline"],           "cta_eligible": True},   # Fri
    5: {"format": "quote_react",         "image_templates": ["none"],                               "cta_eligible": False},  # Sat
    6: None,  # Sun — reply-only day
}
FORMAT_LABELS = {
    "mechanism_explainer": "Mechanism Explainer",
    "hot_take": "Hot Take",
    "build_log": "Build Log",
    "india_cost": "India Cost Check",
    "quote_react": "Quote React",
}
WEEKDAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT + OUTPUT CONTRACT
# ══════════════════════════════════════════════════════════════════════════════

POST_SYSTEM_PROMPT = """
You are the writing voice behind the Threads account @vipinailabs.

ACCOUNT IDENTITY
- Persona: a builder who tests AI tools hands-on and reports real costs and real results, specifically for Indian developers deciding whether something is worth their time or money.
- Niche: LLM internals and agentic AI systems — mechanisms, not hype. Attention, quantization, fine-tuning, RAG, agent tooling, GPU/inference economics.
- Never write like a press release, a listicle, or a course ad. Write like someone explaining what they just learned to a peer over chai.

VOICE RULES
1. Vary sentence construction. NEVER reuse these patterns more than once every 10 posts:
   - "It's not X, it's Y."
   - "X isn't the real story. Y is."
   - "The raw number isn't the story."
   Before finalizing a post, check it against the last 10 posts' opening and closing lines. If the rhythm matches, rewrite.
2. Prefer concrete numbers, named tools, and specific failure cases over general claims.
3. One idea per post. If you need three sub-points, that's a sign the idea needs a diagram, not more sentences.
4. End most posts with a real, narrow question the author could actually answer if someone replied — not a rhetorical binary ("X or Y?") unless the format specifically calls for one.
5. Do not include a follow CTA or a link unless the OUTPUT FORMAT below says this post is flagged for one. When in doubt, leave both out.

OUTPUT FORMAT
Return JSON only:
{
  "format": "mechanism_explainer | build_log | hot_take | india_cost | quote_react",
  "hook": "first line, must earn the next line",
  "body": "full post text, no CTA/link unless cta_included is true",
  "cta_included": boolean,
  "tag": "AI | AgenticAI | cloudcomputing | <other relevant single tag>",
  "image_template": "three_stage_flow | single_stat_hero | before_after | annotated_screenshot | timeline | none",
  "numeric_claims": ["list every specific number/stat used in the post, for a fact-check pass before posting"],
  "reply_seed": "one honest, specific answer to your own closing question — used to seed the reply thread if no one answers within a few hours"
}

FORMAT-SPECIFIC RULES
- mechanism_explainer: only use image_template "three_stage_flow" or "timeline" if the concept is genuinely sequential. If it's a trade-off or comparison, use "before_after" instead — do not force a 3-step shape onto a 2-sided idea.
- build_log: first person, present tense, something that actually went wrong or surprised you today while building orbitailabs. image_template should usually be "annotated_screenshot" or "none".
- hot_take: one stat, one sentence of context, one question. image_template "single_stat_hero" or "none". Keep under 400 characters.
- india_cost: must include an actual ₹ figure or a named Indian cloud/hardware context (RunPod India pricing, AWS Mumbai, a consumer GPU price in India, a comparison to a developer salary). image_template "single_stat_hero" or "before_after".
- quote_react: written as a reaction to a specific claim (you will be given the source post's text as input) — agree, disagree, or add a missing angle. No image. No CTA.
""".strip()

POST_PROMPT_TEMPLATE = """
Here's research/context on this topic (may include recent sources — use only what's specific and ACCURATE, never invent numbers or mechanisms):
{research}

LOCKED FORMAT for this post: {format}
Topic: {topic}

{cta_note}

For "image_template", choose ONE of exactly these options (today's allowed set — never pick
anything outside this list): {image_template_options}
{extra_context}
Follow the FORMAT-SPECIFIC RULES for "{format}" from your system prompt exactly, and the VOICE
RULES throughout. Keep the body tight and Threads-native — short lines, a blank line between
beats, plain text only, under 500 characters total including any CTA/link (those get appended
after, so leave room if cta_included is true).

Return JSON only, exactly matching the OUTPUT FORMAT keys from your system prompt: format, hook,
body, cta_included, tag, image_template, numeric_claims, reply_seed.
""".strip()

POST_REQUIRED_KEYS = ["format", "hook", "body", "cta_included", "tag", "image_template", "numeric_claims", "reply_seed"]

NAMED_REPETITIVE_PATTERNS = [
    (r"\bit'?s not [^.!?]{1,40}, it'?s\b", "it's not X, it's Y"),
    (r"\b[^.!?]{1,40} isn'?t the real story\b", "X isn't the real story, Y is"),
    (r"\bthe raw number isn'?t the story\b", "the raw number isn't the story"),
]


def find_matched_patterns(text: str) -> list:
    """Which of the 3 named recurring-pattern openers/closers (Voice Rule 1) appear in this text."""
    lowered = text.lower()
    return [label for pattern, label in NAMED_REPETITIVE_PATTERNS if re.search(pattern, lowered)]


# ══════════════════════════════════════════════════════════════════════════════
# HUMANIZER  (post-generation rewrite pass — same content, less "AI-sounding")
# ══════════════════════════════════════════════════════════════════════════════

HUMANIZER_SYSTEM_PROMPT = """
You are an expert human editor.

Your job is to rewrite the text below so it sounds like it was written by a real, thoughtful human — NOT by an AI.

IMPORTANT:
Do not merely replace words with synonyms. Rewrite the thinking, rhythm, sentence structure, and flow.

### REMOVE AI SLOP

Aggressively remove:

* Generic introductions
* "In today's fast-paced world..."
* "In the ever-evolving landscape..."
* "It's important to note that..."
* "Whether you're a beginner or an expert..."
* "Let's dive in..."
* "Here's the thing..."
* "The key takeaway is..."
* "At the end of the day..."
* "This isn't just X, it's Y"
* "Not only X, but also Y"
* Fake enthusiasm
* Corporate/LinkedIn language
* Unnecessary motivational language
* Repetitive conclusions
* Obvious summaries of what was just said
* Excessive headings
* Excessive bullet points
* Artificial transitions
* Overuse of em dashes
* Overly polished sentences
* Needless adjectives and adverbs
* Repetitive sentence patterns
* "Furthermore", "Moreover", "Additionally", "However" when they aren't genuinely needed
* Generic claims such as "This can revolutionize..."
* Empty phrases that sound impressive but say nothing

### MAKE IT SOUND HUMAN

Use:

* Natural sentence lengths
* Short sentences mixed with longer ones
* Contractions where appropriate
* Casual phrasing when the context allows it
* Specific examples instead of vague claims
* Opinions when the original writer clearly has one
* Natural transitions
* Slight imperfections in rhythm
* Direct language
* Concrete words
* A conversational tone
* Personality without forcing jokes
* Confidence without sounding like a marketing brochure

Don't make every sentence perfectly structured.

Real people don't write like textbooks.

### PRESERVE THE ORIGINAL THINKING

Do NOT:

* Change the meaning
* Invent facts
* Add information that wasn't there
* Remove important technical details
* Change numbers, names, examples, or claims
* Turn a simple explanation into something complicated
* Make the writing unnecessarily informal

Keep the author's actual ideas.

Improve how those ideas are expressed.

### IMPORTANT RULE

Don't try to "sound human" by deliberately adding mistakes.

No fake typos.

No unnecessary slang.

No forced humor.

No random "honestly", "literally", "basically", etc.

Human writing comes from natural thought and clear expression — not manufactured imperfections.

### STYLE TEST

Before returning the final version, ask yourself:

"If I saw this on the internet, would I immediately think an AI generated it?"

If the answer is yes, rewrite it again.

Then ask:

"Does this sound like one specific person actually had something to say?"

If not, rewrite it again.

### FINAL OUTPUT

Return ONLY the rewritten content.

Do not explain what you changed.

Do not mention AI detection.

Do not mention this prompt.
""".strip()

HUMANIZER_USER_PROMPT = """
Rewrite the text below following your instructions exactly.

Two things specific to this text, since it's a short social media post and not an article:
- Keep it roughly the same length — this is a length-budgeted Threads post, not free-form prose.
- Don't add headings or bullet points that weren't already there. Keep existing blank lines between beats/paragraphs; that spacing is intentional for how Threads renders short posts.
- Do not add hashtags, links, a follow line, or any sign-off — none of those belong in this text.

TEXT TO REWRITE:
{text}

Return ONLY the rewritten post text. Nothing else.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# FACT-CHECK PASS  (cross-check numeric_claims against the research brief)
# ══════════════════════════════════════════════════════════════════════════════
# Not a real external fact-check — there's no such API wired in. This is a
# heuristic: does each claimed number appear in the same research brief the
# writer saw? If everything checks out, skip the extra LLM call entirely. If
# something doesn't, ask the model to soften/remove just that number.

FACT_CHECK_SYSTEM = """
You are a careful fact-checking editor for a technical social media account. You are given
research sources and a list of specific numeric claims pulled from a post. Your only job is
to identify which claims are NOT explicitly supported by the sources, and rewrite the post so
any unsupported number becomes a qualitative statement instead — change nothing else about
the post: same structure, same voice, same other claims, same rough length.
Return valid JSON only: no markdown, no prose.
""".strip()

FACT_CHECK_USER_TEMPLATE = """
RESEARCH SOURCES:
{research}

NUMERIC CLAIMS PULLED FROM THE POST:
{claims}

POST BODY:
\"\"\"
{body}
\"\"\"

For each claim, decide if it is explicitly supported by the sources above. If ANY claim is
NOT supported, rewrite the POST BODY so that specific number is softened to a qualitative
statement (e.g. "a few hundred dollars" instead of an invented exact figure) — leave every
other sentence, number, and claim exactly as it is. If ALL claims are supported, return the
post body completely unchanged.

Return a single JSON object with EXACTLY these keys:
{{
  "unsupported_claims": ["claims that were NOT supported, empty list if all were supported"],
  "body": "the post body, corrected if needed, otherwise identical to the input"
}}
""".strip()


def fact_check_claims(claims: list, research: str, body: str) -> tuple:
    """Returns (possibly-corrected body, flagged claims list). Skips the LLM call
    entirely if every claim already appears verbatim in the research brief."""
    claims = [str(c) for c in claims if str(c).strip()]
    if not claims:
        return body, []
    research_lower = (research or "").lower()
    unsupported = [c for c in claims if c.lower() not in research_lower]
    if not unsupported:
        return body, []
    print(f"  [FactCheck] {len(unsupported)}/{len(claims)} claim(s) not found verbatim in research — asking model to verify...")
    prompt = FACT_CHECK_USER_TEMPLATE.format(
        research=(research or "")[:3000],
        claims="\n".join(f"- {c}" for c in claims),
        body=body,
    )
    try:
        raw = generate_text(prompt, FACT_CHECK_SYSTEM)
        data = _parse_json_response(raw)
        corrected_body = _clean_model_output(str(data.get("body", body))) or body
        flagged = [str(c) for c in (data.get("unsupported_claims") or [])]
        if flagged:
            print(f"  [FactCheck] Flagged & auto-softened: {flagged}")
        return corrected_body, flagged
    except Exception as e:
        print(f"  [FactCheck] Auto-correction failed ({e}) — flagging only, body left as-is: {unsupported}")
        return body, unsupported


# ══════════════════════════════════════════════════════════════════════════════
# GEMINI RETRY + FALLBACK CHAIN  (key1 → key2 → Euron)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_retry_seconds(error: Exception) -> int:
    match = re.search(r"retryDelay['\"]:\s*['\"](\d+)s", str(error))
    return min(int(match.group(1)), 60) if match else RETRY_BASE_SECONDS


def _is_quota_error(error: Exception) -> bool:
    return "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error) or "quota" in str(error).lower()


def _is_retryable_server_error(error: Exception) -> bool:
    msg = str(error).lower()
    return "503" in msg or "unavailable" in msg or "high demand" in msg


def _is_daily_quota_exhausted(error: Exception) -> bool:
    s = str(error)
    return "PerDay" in s or "GenerateRequestsPerDay" in s or ("limit: 0" in s and "429" in s)


def _call_euron(prompt: str, system_instruction: str) -> str:
    if not EURON_API_KEY:
        raise RuntimeError("EURON_API_KEY not set.")
    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": prompt},
    ]
    for attempt in range(1, 4):
        resp = requests.post(
            "https://api.euron.one/api/v1/euri/chat/completions",
            headers={"Authorization": f"Bearer {EURON_API_KEY}", "Content-Type": "application/json"},
            json={"model": EURON_MODEL, "messages": messages},
            timeout=90,
        )
        if resp.status_code == 429:
            wait = 20 * attempt
            print(f"  [Euron] 429 rate limit, attempt {attempt}/3. Waiting {wait}s...")
            time.sleep(wait)
            continue
        if not resp.ok:
            raise RuntimeError(
                f"Euron API {resp.status_code} for model '{EURON_MODEL}': {resp.text[:300]}"
            )
        return resp.json()["choices"][0]["message"]["content"].strip()
    raise RuntimeError("Euron API failed after 3 attempts.")


def generate_text(prompt: str, system_instruction: str) -> str:
    """Call Gemini with key rotation (key1 → key2 → Euron fallback)."""
    api_keys = [k for k in [GEMINI_API_KEY, GEMINI_API_KEY_2] if k]
    models_to_try = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]
    last_error = None

    for key_index, api_key in enumerate(api_keys):
        client = genai.Client(api_key=api_key)
        key_label = f"key#{key_index + 1} (...{api_key[-6:]})"
        daily_exhausted = False
        print(f"  [Gemini] Trying {key_label}")

        for model_id in models_to_try:
            if daily_exhausted:
                break
            config = types.GenerateContentConfig(system_instruction=system_instruction)
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response = client.models.generate_content(
                        model=model_id, contents=prompt, config=config
                    )
                    print(f"  [Gemini] Success with {model_id} on {key_label}")
                    return response.text.strip()
                except Exception as e:
                    if _is_quota_error(e) or _is_retryable_server_error(e):
                        last_error = e
                        if _is_daily_quota_exhausted(e):
                            next_key = f"key#{key_index + 2}" if key_index + 1 < len(api_keys) else "Euron fallback"
                            print(f"  [Gemini] Daily quota exhausted on {key_label}. Switching to {next_key}.")
                            daily_exhausted = True
                            break
                        wait = _parse_retry_seconds(e)
                        kind = "quota (429)" if _is_quota_error(e) else "overloaded (503)"
                        print(f"  [Gemini] {kind} on {model_id} ({key_label}), attempt {attempt}/{MAX_RETRIES}. Retrying in {wait}s...")
                        if attempt < MAX_RETRIES:
                            time.sleep(wait)
                        else:
                            print(f"  [Gemini] Retries exhausted for {model_id}, trying next model.")
                            break
                    else:
                        raise

    # All Gemini keys exhausted → try Euron
    if EURON_API_KEY:
        print("  [Euron] All Gemini keys exhausted. Falling back to Euron...")
        return _call_euron(prompt, system_instruction)

    raise last_error or RuntimeError(
        "All Gemini keys exhausted and no Euron key configured. Try again tomorrow."
    )


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Research with Exa
# ══════════════════════════════════════════════════════════════════════════════

def _exa_search(exa, topic: str, niche: str, hours: int = None, fresh: bool = True, query: str = None):
    """Run an Exa search.

    fresh=True  → news mode: recent releases, restricted to the last `hours`.
    fresh=False → evergreen mode: best explanatory sources, no date restriction.
    query overrides the default "latest {topic} — {niche}" query (used by the
    quote_react claim search).
    """
    kwargs = dict(
        type="auto",
        num_results=5,
        contents={"text": {"max_characters": 800}, "highlights": {"num_sentences": 3}},
    )
    if fresh:
        now = datetime.now(timezone.utc)
        kwargs["query"] = query or f"latest {topic} — {niche}"
        kwargs["category"] = "news"
        kwargs["start_published_date"] = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        kwargs["end_published_date"]   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        kwargs["query"] = query or f"{topic} — how it works, explained ({niche})"
    return exa.search(**kwargs)


def _format_research_brief(results) -> str:
    """Render Exa search results into the plain-text brief the writer/fact-checker see."""
    lines = []
    for i, result in enumerate(results.results, 1):
        title      = result.title or "Untitled"
        url        = result.url
        published  = getattr(result, "published_date", None) or "recent"
        text       = (result.text or "")[:600].strip()
        highlights = result.highlights or []

        lines.append(f"Source {i}: {title}")
        lines.append(f"Published: {published}")
        lines.append(f"URL: {url}")
        if highlights:
            lines.append(f"Key insight: {highlights[0]}")
        if text:
            lines.append(f"Context: {text[:300]}...")
        lines.append("")
    return "\n".join(lines)


def research_topic(topic: str, niche: str, fresh: bool = True) -> str:
    """Return a research brief for the topic.

    fresh=True (hot_take): restrict to the last 48h, widen to 7d if too few.
    fresh=False (evergreen formats): best explanatory sources, no date filter.
    """
    print("\n[ Step 1 ] Researching topic with Exa...")

    exa = Exa(api_key=EXA_API_KEY)
    if not fresh:
        results = _exa_search(exa, topic, niche, fresh=False)
        print(f"  Evergreen search — found {len(results.results)} explanatory sources.")
    else:
        results = _exa_search(exa, topic, niche, NEWS_WINDOW_HOURS)
        print(f"  Searched last {NEWS_WINDOW_HOURS}h — found {len(results.results)} sources.")
        # If nothing fresh in the strict window, widen so we never post on stale/no research.
        if len(results.results) < 2 and NEWS_FALLBACK_HOURS > NEWS_WINDOW_HOURS:
            print(f"  Too few fresh sources — widening to last {NEWS_FALLBACK_HOURS}h.")
            results = _exa_search(exa, topic, niche, NEWS_FALLBACK_HOURS)
            print(f"  Found {len(results.results)} sources in widened window.")

    brief = _format_research_brief(results)
    print()
    return brief


def research_claim_to_react_to(niche: str):
    """Source a real, recent AI opinion/claim for the quote_react format.

    Returns (topic, research_brief, claim_text) — `claim_text` is the literal
    source post/claim the model reacts to; `topic`/`research_brief` keep the
    same shape research_topic() returns so downstream code (infographic
    alignment, history logging) doesn't need a quote_react special case.
    """
    print("\n[ Step 1 ] Sourcing a claim to react to (Exa)...")
    exa = Exa(api_key=EXA_API_KEY)
    query = f"AI take OR opinion OR claim — {niche}"

    results = _exa_search(exa, "", niche, NEWS_WINDOW_HOURS, query=query)
    print(f"  Searched last {NEWS_WINDOW_HOURS}h — found {len(results.results)} sources.")
    if len(results.results) < 1 and NEWS_FALLBACK_HOURS > NEWS_WINDOW_HOURS:
        print(f"  Nothing fresh — widening to last {NEWS_FALLBACK_HOURS}h.")
        results = _exa_search(exa, "", niche, NEWS_FALLBACK_HOURS, query=query)
        print(f"  Found {len(results.results)} sources in widened window.")

    if not results.results:
        raise RuntimeError("No claim found to react to, even after widening the search window.")

    top = results.results[0]
    claim_text = (top.highlights[0] if top.highlights else (top.text or "")[:400]).strip()
    topic = top.title or "a recent AI claim"

    brief = _format_research_brief(results)
    print()
    return topic, brief, claim_text


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Generate post (JSON) + quality gates
# ══════════════════════════════════════════════════════════════════════════════

def _clean_model_output(text: str) -> str:
    """Strip wrapping quotes and markdown emphasis markers models sometimes add."""
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}(.+?)_{1,2}', r'\1', text)
    return text.strip()


def _parse_json_response(raw: str) -> dict:
    cleaned = raw.strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def humanize_post(post: str) -> str:
    """Rewrite the generated post so it reads like a specific person wrote it,
    not an AI — same meaning, numbers, and claims, different phrasing/rhythm.

    Runs through the same Gemini/Euron fallback chain as generation. If the
    rewrite fails or comes back empty, the original post is kept as-is so a
    humanizing hiccup never kills the daily post.
    """
    print("  Humanizing post...")
    try:
        rewritten = generate_text(HUMANIZER_USER_PROMPT.format(text=post), HUMANIZER_SYSTEM_PROMPT)
    except Exception as e:
        print(f"  [Humanize] Skipped — {e}. Keeping original phrasing.")
        return post
    rewritten = _clean_model_output(rewritten)
    return rewritten if rewritten else post


def get_closing_line(body: str) -> str:
    lines = [l.strip() for l in body.strip().split("\n") if l.strip()]
    return lines[-1] if lines else ""


def is_repetitive(hook: str, closing_line: str, history: list) -> tuple:
    """Fuzzy-compare hook/closing against the last 10 history entries, plus a
    hard check on the 3 named recurring patterns from Voice Rule 1."""
    recent = history[-10:]
    for entry in recent:
        for a, b, label in (
            (hook, entry.get("hook", ""), "hook"),
            (closing_line, entry.get("closing_line", ""), "closing line"),
        ):
            if a and b:
                ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
                if ratio > 0.72:
                    return True, f"{label} too similar to a recent post ({ratio:.2f} match): \"{b}\""
    for label in find_matched_patterns(hook + " " + closing_line):
        if any(label in entry.get("matched_patterns", []) for entry in recent):
            return True, f"reused the '{label}' pattern within the last 10 posts"
    return False, ""


def cta_cap_allows(history: list) -> bool:
    """At most 1 CTA-bearing post allowed in any trailing window of 8 (~15%)."""
    recent = history[-7:]
    return sum(1 for e in recent if e.get("cta_included")) == 0


def pick_valid_template(model_choice: str, allowed_options: list, history: list) -> str:
    """Enforce template variety: if the model's choice has appeared more than 3
    times in the last 8 posts, swap to whichever allowed option was used least
    recently. No enforcement when the format only has one valid option."""
    if len(allowed_options) <= 1:
        return allowed_options[0] if allowed_options else model_choice
    recent = history[-8:]
    uses = sum(1 for e in recent if e.get("image_template") == model_choice)
    if model_choice in allowed_options and uses <= 3:
        return model_choice
    last_seen = {}
    for opt in allowed_options:
        idxs = [i for i, e in enumerate(recent) if e.get("image_template") == opt]
        last_seen[opt] = max(idxs) if idxs else -1
    swapped = min(allowed_options, key=lambda o: last_seen[o])
    print(f"  [TemplateVariety] '{model_choice}' overused/invalid recently — swapping to '{swapped}'.")
    return swapped


def generate_post_json(format_key: str, topic: str, research: str, cta_eligible_today: bool,
                        allowed_templates: list, extra_context: str = "") -> dict:
    """Call Gemini for the locked format, parse+validate the JSON output contract."""
    cta_note = (
        "This IS today's one CTA-eligible slot. You may set cta_included true if a follow "
        "CTA is genuinely earned here; it's also fine to leave it false."
        if cta_eligible_today else
        "This slot is NOT CTA-eligible today. cta_included MUST be false, and the body must "
        "not contain a follow line or any link."
    )
    prompt = POST_PROMPT_TEMPLATE.format(
        research=research[:2500],
        format=format_key,
        topic=topic,
        cta_note=cta_note,
        image_template_options=", ".join(allowed_templates),
        extra_context=("\n" + extra_context if extra_context else ""),
    )

    last_err = ""
    for attempt in range(1, 3):  # one retry
        user = prompt if attempt == 1 else prompt + f"\n\nPREVIOUS ATTEMPT FAILED: {last_err}\nReturn corrected JSON only."
        raw = generate_text(user, POST_SYSTEM_PROMPT)
        try:
            data = _parse_json_response(raw)
            missing = [k for k in POST_REQUIRED_KEYS if k not in data]
            if missing:
                raise ValueError(f"missing keys: {missing}")
            return data
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            print(f"  [Gemini] Post JSON parse failed (attempt {attempt}): {last_err}")
    raise RuntimeError(f"Post generation failed to produce valid JSON: {last_err}")


def build_final_post(body: str, tag: str, topic: str, cta_final: bool) -> tuple:
    """Append the topic tag (always) and the follow-CTA/portfolio-link block
    (only when cta_final), enforcing the 500-char budget the same way as before."""
    topic_tag = validate_tag(tag, body, topic)
    if cta_final:
        link_line = f"\n\n{PORTFOLIO_CTA}{PORTFOLIO_LINK}" if PORTFOLIO_LINK else ""
        footer = f"\n\n{topic_tag}\n\n{FOLLOW_CTA}{link_line}"
    else:
        footer = f"\n\n{topic_tag}"
    body_limit = 500 - len(footer)

    # If the body is over its budget, ask model to shorten (max 2 attempts)
    for shorten_attempt in range(2):
        if len(body) <= body_limit:
            break
        print(f"  Body is {len(body)} chars (limit {body_limit}) — asking model to shorten (attempt {shorten_attempt + 1}/2)...")
        shorten_prompt = (
            f"This Threads post body is {len(body)} characters, over the {body_limit}-character budget.\n\n"
            f"Shorten it to strictly under {body_limit - 10} characters while keeping the hook and the specific "
            f"details that matter. Maintain the voice: precise, direct, natural human phrasing (not corporate or "
            f"AI-sounding). Use line breaks where natural. No starting with 'I'.\n"
            f"Plain text only — no markdown, no links, no sign-off.\n\n"
            f"Original post:\n{body}\n\n"
            f"Output ONLY the shortened post. Nothing else."
        )
        body = generate_text(shorten_prompt, POST_SYSTEM_PROMPT)
        body = _clean_model_output(body)

    # Last-resort truncation at word boundary if the body is still over budget
    if len(body) > body_limit:
        print(f"  Body still {len(body)} chars after shortening — truncating at word boundary...")
        truncated  = body[:body_limit - 1]
        last_space = truncated.rfind(" ")
        body = (truncated[:last_space] if last_space > body_limit * 0.8 else truncated).rstrip(".,;:!?") + "…"
        print(f"  Truncated body to {len(body)} chars.")

    return body + footer, topic_tag


def generate_post(format_key: str, topic: str, research: str, cta_eligible_today: bool,
                   allowed_templates: list, history: list, extra_context: str = "") -> dict:
    """Generate + gate-check one post. Returns everything needed to schedule it
    and to append a history record (see append_history)."""
    print("[ Step 2 ] Generating post with Gemini...")
    data = generate_post_json(format_key, topic, research, cta_eligible_today, allowed_templates, extra_context)

    body = _clean_model_output(str(data.get("body", "")))
    if HUMANIZE_POST:
        body = humanize_post(body)

    numeric_claims = [str(c) for c in (data.get("numeric_claims") or [])]
    body, flagged_claims = fact_check_claims(numeric_claims, research, body)

    hook = str(data.get("hook", "")).strip() or (body.split("\n")[0].strip() if body else "")
    closing_line = get_closing_line(body)

    repetitive, reason = is_repetitive(hook, closing_line, history)
    if repetitive:
        print(f"  [Repetition] {reason} — asking model to rewrite the opening/closing (up to 2 attempts)...")
        for _ in range(2):
            rewrite_prompt = (
                f"Your post's opening or closing line is too close to a recent post — {reason}\n\n"
                f"Rewrite ONLY the opening line and/or the closing line so it reads differently: same "
                f"meaning, same facts, a different angle or phrasing. Keep the rest of the body unchanged.\n\n"
                f"Full post body:\n{body}\n\n"
                f"Output ONLY the corrected post body. Nothing else."
            )
            body = _clean_model_output(generate_text(rewrite_prompt, POST_SYSTEM_PROMPT))
            hook = body.split("\n")[0].strip() if body else hook
            closing_line = get_closing_line(body)
            repetitive, reason = is_repetitive(hook, closing_line, history)
            if not repetitive:
                break
        if repetitive:
            print(f"  [Repetition] Still flagged after retries — posting anyway. {reason}")

    matched_patterns = find_matched_patterns(hook + " " + closing_line)

    cta_final = bool(data.get("cta_included")) and cta_eligible_today and cta_cap_allows(history)
    image_template = pick_valid_template(str(data.get("image_template", "none")), allowed_templates, history)

    post_text, topic_tag = build_final_post(body, str(data.get("tag", "")), topic, cta_final)

    print(f"\n  Generated post ({format_key}):\n  {'─'*50}")
    for line in post_text.split("\n"):
        print(f"  {line}")
    print(f"  {'─'*50}")
    print(f"  Character count : {len(post_text)}/500")
    print(f"  CTA included    : {cta_final}")
    print(f"  Image template  : {image_template}")
    print(f"  Tag             : {topic_tag}")
    if flagged_claims:
        print(f"  Flagged claims  : {flagged_claims}")
    print()

    return {
        "post_text": post_text,
        "hook": hook,
        "closing_line": closing_line,
        "matched_patterns": matched_patterns,
        "cta_included": cta_final,
        "image_template": image_template,
        "tag": topic_tag,
        "numeric_claims": numeric_claims,
        "flagged_claims": flagged_claims,
        "reply_seed": str(data.get("reply_seed", "")),
        "format": format_key,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Schedule to Buffer
# ══════════════════════════════════════════════════════════════════════════════

def schedule_to_buffer(post_text: str, image_url: str = None) -> str:
    """Push the post to Buffer via GraphQL. Schedules 5 minutes from now.

    If image_url is given (a public URL), it is attached as a Threads image via
    Buffer's assets field. Buffer cannot upload files — the URL must be public.
    """
    print("[ Step 3 ] Scheduling to Buffer...")
    if image_url:
        print(f"  [Buffer] Attaching infographic: {image_url}")

    due_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    # schedulingType: automatic + mode: customScheduled → respect the exact dueAt time
    # (schedulingType: automatic alone would use Buffer's own queue slots)
    # assets[].image.url attaches an image (only added when an image_url is given).
    asset_decl  = ", $imageUrl: String!" if image_url else ""
    asset_field = "assets: [{ image: { url: $imageUrl } }]," if image_url else ""
    mutation = f"""
    mutation CreatePost($text: String!, $channelId: ChannelId!, $dueAt: DateTime{asset_decl}) {{
      createPost(input: {{
        text: $text,
        channelId: $channelId,
        schedulingType: automatic,
        mode: customScheduled,
        {asset_field}
        dueAt: $dueAt
      }}) {{
        ... on PostActionSuccess {{
          post {{
            id
            text
          }}
        }}
        ... on MutationError {{
          message
        }}
      }}
    }}
    """

    MAX_BUFFER_RETRIES = 5
    # Exponential backoff: 60s, 120s, 180s, 240s, 300s
    BUFFER_BACKOFFS = [60, 120, 180, 240, 300]

    for attempt in range(1, MAX_BUFFER_RETRIES + 1):
        response = requests.post(
            "https://api.buffer.com/graphql",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {BUFFER_API_KEY}",
            },
            json={
                "query": mutation,
                "variables": {
                    "text": post_text,
                    "channelId": BUFFER_CHANNEL_ID,
                    "dueAt": due_at,
                    **({"imageUrl": image_url} if image_url else {}),
                },
            },
            timeout=30,
        )

        print(f"  [Buffer] HTTP {response.status_code} on attempt {attempt}")

        # --- HTTP-level rate limit ---
        if response.status_code == 429:
            if attempt < MAX_BUFFER_RETRIES:
                wait = BUFFER_BACKOFFS[attempt - 1]
                print(f"  [Buffer] HTTP 429 on attempt {attempt}/{MAX_BUFFER_RETRIES}. Waiting {wait}s...")
                time.sleep(wait)
                continue
            # Exhausted retries on HTTP 429
            break

        # Print body BEFORE raise_for_status so 4xx errors show Buffer's error message
        if response.status_code != 200:
            print(f"  [Buffer] Error body: {response.text}")
        response.raise_for_status()
        data = response.json()

        # Always print the raw response so we can debug issues
        print(f"  [Buffer] Raw response: {json.dumps(data, indent=2)}")

        # --- GraphQL-level rate limit (Buffer returns 200 OK with errors in body) ---
        if "errors" in data:
            errors = data["errors"]
            error_codes = [e.get("extensions", {}).get("code", "") for e in errors]
            if "RATE_LIMIT_EXCEEDED" in error_codes:
                if attempt < MAX_BUFFER_RETRIES:
                    wait = BUFFER_BACKOFFS[attempt - 1]
                    print(f"  [Buffer] RATE_LIMIT_EXCEEDED on attempt {attempt}/{MAX_BUFFER_RETRIES}. Waiting {wait}s...")
                    time.sleep(wait)
                    continue
                # Exhausted retries on rate limit — fall through to fallback
                break
            # Any other GraphQL error is a real error — raise immediately
            raise RuntimeError(f"Buffer API error: {errors}")

        # --- Check for MutationError in the union type ---
        result = data.get("data", {}).get("createPost", {})
        if "message" in result:
            raise RuntimeError(f"Buffer mutation error: {result['message']}")

        # --- Success ---
        post = result.get("post", {})
        post_id = post.get("id", "unknown")
        scheduled_at = due_at
        print(f"  Scheduled! Buffer Post ID : {post_id}")
        print(f"  Publish time (UTC)        : {scheduled_at}\n")
        return post_id

    # All retries exhausted due to rate limiting — save post so it isn't lost
    fallback_path = os.path.join(_script_dir, "..", "pending_post.txt")
    with open(fallback_path, "w") as fh:
        fh.write(f"DUE_AT: {due_at}\n\n{post_text}")
    print(f"  [Buffer] All {MAX_BUFFER_RETRIES} attempts failed — Buffer rate limit (15-min window).")
    print(f"  [Buffer] Post saved to pending_post.txt for manual scheduling or a re-run.")
    print(f"  [Buffer] This is NOT a code error. Re-trigger the workflow in 15+ minutes.\n")
    return "PENDING_RATE_LIMITED"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2.5 — Infographic image (optional)
# ══════════════════════════════════════════════════════════════════════════════

def build_infographic_image(research: str, topic: str, preview: bool,
                            post: str = "", image_template: str = "three_stage_flow"):
    """Render the infographic and (unless preview) host it for Buffer.

    `post` aligns the diagram to the post: it visualizes the SAME solution/
    concept the post centers on. `image_template` picks which layout to build
    (see scripts/infographic_templates.py) — chosen by generate_post()'s
    template-variety gate, not just the raw model output.

    Returns a public image URL (real run), a local PNG path (preview), or None if
    anything fails — in which case the post falls back to text-only so a single
    rendering hiccup never kills the daily post.
    """
    try:
        print("\n[ Step 2.5 ] Building infographic image...")
        content  = infographic.generate_infographic_content(
            research, topic, generate_text, post=post, image_template=image_template,
        )
        out_dir  = os.path.join(_script_dir, "..", "output")
        os.makedirs(out_dir, exist_ok=True)
        png_path = os.path.abspath(os.path.join(out_dir, "infographic.png"))
        infographic.render_infographic(content, png_path)
        if preview:
            return png_path
        return infographic.upload_to_imgbb(png_path)
    except Exception as e:
        print(f"  [Infographic] Skipped — {e}. Falling back to text-only post.")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# HISTORY  (rolling record of recent posts — powers the quality gates above,
# and needs to survive across separate GitHub Actions runs, so the workflow
# commits scripts/post_history.json back to the repo after each real run)
# ══════════════════════════════════════════════════════════════════════════════

HISTORY_PATH        = os.path.join(_script_dir, "post_history.json")
HISTORY_MAX_ENTRIES = 60


def load_history() -> list:
    try:
        with open(HISTORY_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def append_history(history: list, result: dict, post_id: str, weekday: int) -> list:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "weekday_ist": weekday,
        "format": result["format"],
        "hook": result["hook"],
        "closing_line": result["closing_line"],
        "matched_patterns": result["matched_patterns"],
        "cta_included": result["cta_included"],
        "image_template": result["image_template"],
        "tag": result["tag"],
        "numeric_claims": result["numeric_claims"],
        "flagged_claims": result["flagged_claims"],
        "reply_seed": result["reply_seed"],
        "buffer_post_id": post_id,
    }
    history = history + [entry]
    history = history[-HISTORY_MAX_ENTRIES:]
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)
    return history


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main(preview: bool = False):
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    weekday = ist_now.weekday()  # Monday=0 .. Sunday=6
    rotation = DAY_ROTATION.get(weekday)

    print(f"\n{'='*60}")
    print(f"  Threads Post Agent — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    if preview:
        print(f"  MODE: PREVIEW (no Buffer scheduling, history not updated)")
    print(f"{'='*60}")
    print(f"  Day (IST) : {WEEKDAY_LABELS[weekday]}")

    if rotation is None:
        print(f"  Sunday — reply-only day. No new post generated.")
        print(f"{'='*60}\n")
        return

    format_key         = rotation["format"]
    allowed_templates   = rotation["image_templates"]
    cta_eligible_today  = rotation["cta_eligible"]

    print(f"  Niche     : {NICHE}")
    print(f"  Format    : {format_key} — {FORMAT_LABELS.get(format_key, format_key)}")
    print(f"  CTA today : {cta_eligible_today}")
    print(f"{'='*60}\n")

    try:
        history = load_history()

        extra_context = ""
        if format_key == "quote_react":
            topic, research, claim_text = research_claim_to_react_to(NICHE)
            extra_context = f"THE CLAIM/POST YOU ARE REACTING TO:\n\"\"\"\n{claim_text}\n\"\"\""
        else:
            topic = pick_topic_for_format(format_key)
            research = research_topic(topic, NICHE, fresh=(format_key == "hot_take"))
            if format_key == "india_cost":
                extra_context = "Ground this in a REAL cost figure or a named Indian cloud/hardware context from the research above — never invent one."

        result = generate_post(format_key, topic, research, cta_eligible_today, allowed_templates, history, extra_context)

        image_ref = None
        if INCLUDE_INFOGRAPHIC and result["image_template"] != "none":
            image_ref = build_infographic_image(research, topic, preview, post=result["post_text"], image_template=result["image_template"])
        else:
            print(f"  [Infographic] Skipped (image_template={result['image_template']}).")

        if preview:
            print(f"{'='*60}")
            print(f"  PREVIEW ONLY — post NOT sent to Buffer, history NOT updated.")
            if image_ref:
                print(f"  Infographic saved at: {image_ref}")
            print(f"  reply_seed (for manual use if replies are slow): {result['reply_seed']}")
            print(f"  Run without --preview to schedule it.")
            print(f"{'='*60}\n")
            return

        post_id = schedule_to_buffer(result["post_text"], image_ref)
        append_history(history, result, post_id, weekday)

        print(f"{'='*60}")
        if post_id == "PENDING_RATE_LIMITED":
            print(f"  WARNING: Buffer was rate-limited. Post saved to pending_post.txt.")
            print(f"  Re-trigger the workflow in 15+ minutes to retry.")
        else:
            print(f"  Done! Post queued in Buffer → will publish to Threads")
            print(f"  Buffer ID : {post_id}")
        print(f"  reply_seed (for manual use if replies are slow): {result['reply_seed']}")
        print(f"{'='*60}\n")

    except Exception as e:
        import traceback
        print(f"\n  ERROR: {e}")
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    main(preview="--preview" in sys.argv)
