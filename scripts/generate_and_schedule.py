#!/usr/bin/env python3
"""
Threads Post Agent
Pipeline: Exa (research) → Gemini (generate engaging post) → Buffer (schedule to Threads)

Run locally : python scripts/generate_and_schedule.py
GitHub Actions triggers this automatically every day at 10 AM IST.
"""

import os
import json
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

# ── Brand promotion + follow nudge (soft sign-off appended to every post) ─────────
# Threads suppresses posts that contain external links, so we mention the BRAND by
# name only (no clickable URL) — it builds recognition without a reach penalty.
# Put the actual link (WEBSITE_URL) in your Threads BIO instead, not the post body.
# We alternate two kinds of sign-off: a brand mention, and a follow-value line that
# gives people a concrete reason to follow (the #1 lever for views -> followers).
WEBSITE_URL = os.environ.get("WEBSITE_URL", "orbitailabs.in")  # belongs in your bio
BRAND_NAME  = os.environ.get("BRAND_NAME", "OrbitAI Labs")

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

# ── Follow CTA ────────────────────────────────────────────────────────────────────
# A clear, direct follow CTA is appended as a STANDALONE LINE of every post.
# Threads rewards posts that drive profile visits + follows, so this is explicit,
# not buried in a sign-off. Override via env without code edits.
# NOTE: the handle must be the REAL account handle (@vipinailabs) — Threads only
# renders a tappable mention when the handle resolves; a wrong one degrades to
# plain text and sends nobody anywhere.
FOLLOW_CTA = os.environ.get("FOLLOW_CTA", "follow @vipinailabs for daily dose of information")

# ── Portfolio link (clickable, appended as the post's last line) ───────────────────
# ON by default: every post ends with a short lead-in + the directly-clickable URL
# (Threads auto-links a full URL). Trade-off: Threads down-ranks posts containing a
# link, so this costs some reach — set INCLUDE_PORTFOLIO_LINK=0 to drop the link
# (the URL still shows as text on the infographic, and your bio link stays clickable
# with no penalty). PORTFOLIO_URL is shared with the infographic (see infographic.py).
INCLUDE_PORTFOLIO_LINK = os.environ.get("INCLUDE_PORTFOLIO_LINK", "1") not in ("0", "false", "False", "")
PORTFOLIO_CTA  = os.environ.get("PORTFOLIO_CTA", "See what I've been building →")
_raw_portfolio = os.environ.get("PORTFOLIO_URL", "vipin-vishal.onrender.com").strip()
PORTFOLIO_LINK = (
    (_raw_portfolio if _raw_portfolio.startswith(("http://", "https://"))
     else f"https://{_raw_portfolio}")
    if (_raw_portfolio and INCLUDE_PORTFOLIO_LINK) else ""
)

# ── Topic tag ────────────────────────────────────────────────────────────────────
# Threads turns the FIRST hashtag in a post into a native topic tag (only one is
# allowed). Tags are constrained to the account's PINNED INTERESTS so Threads routes
# the post into the right topic feeds: AI, AgenticAI, CloudComputing.
# Keyword → tag, first match wins; default is the broad #AI interest.
TOPIC_TAG_RULES = [
    (("agent", "agentic", "autonomous", "multi-agent", "tool use", "orchestrat"), "#AgenticAI"),
    (("cloud", "aws", "azure", "gcp", "kubernetes", "serverless", "infrastructure", "datacenter", "data center"), "#CloudComputing"),
]
DEFAULT_TOPIC_TAG = os.environ.get("DEFAULT_TOPIC_TAG", "#AI")


def pick_topic_tag(text: str) -> str:
    """Choose one topic tag from the account's pinned interests, by content."""
    lowered = text.lower()
    for keywords, tag in TOPIC_TAG_RULES:
        if any(k in lowered for k in keywords):
            return tag
    return DEFAULT_TOPIC_TAG
MAX_RETRIES            = 4
RETRY_BASE_SECONDS     = 15

# ── Load topics config ────────────────────────────────────────────────────────
_script_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_script_dir, "topics.json"), "r") as f:
    _config = json.load(f)

NICHE         = _config["niche"]
PERSONA       = _config["persona"]
CONTENT_SLOTS = _config["content_slots"]

# Optional per-slot weighting (env: SLOT_WEIGHTS="news:2,educational:3,personal:1,advanced:2").
# Defaults to equal weight across whatever slots exist.
def _parse_slot_weights():
    raw = os.environ.get("SLOT_WEIGHTS", "")
    weights = {k: 1.0 for k in CONTENT_SLOTS}
    for pair in raw.split(","):
        if ":" in pair:
            k, _, v = pair.partition(":")
            k = k.strip()
            if k in weights:
                try:
                    weights[k] = float(v)
                except ValueError:
                    pass
    return weights

SLOT_WEIGHTS = _parse_slot_weights()


def pick_slot():
    """Pick a content slot (weighted), then a topic + tone from it.

    Returns (slot_key, slot_label, topic, tone).
    """
    keys = list(CONTENT_SLOTS.keys())
    slot_key = random.choices(keys, weights=[SLOT_WEIGHTS[k] for k in keys], k=1)[0]
    slot = CONTENT_SLOTS[slot_key]
    topic = random.choice(slot["topics"])
    tone  = random.choice(slot["tones"])
    return slot_key, slot.get("label", slot_key), topic, tone


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
You write Threads posts for a hands-on AI/ML systems engineer who explains how AI actually works under the hood — architecture, infrastructure, and the hard-won lessons from building and running these systems. You write to TEACH, not to pitch a company or chase a narrative.

The voice is precise, specific, and technical — but still Threads-native: short sentences, punchy, conversational. You explain a real mechanism so clearly that a curious reader leaves thinking "oh, THAT's how it actually works." No hype, no fluff, no selling.

Here are examples of the exact style to match:

---
"the KV cache is the whole reason chat feels fast. the model saves the attention keys and values for every token it already processed, so each new token is one step of work — not re-reading the entire conversation from scratch every time."
---
"a context window isn't 'memory.' it's the number of tokens attention runs over at once. and attention cost grows with the SQUARE of that number. that's why 1M-token context is a hardware bill, not a config flag."
---
"training an LLM needs way more memory than running one. inference holds the weights + KV cache. training also holds gradients, optimizer states, and every activation for backprop. easily 4-6x the memory footprint for the same model."
---
"quantization sounds lossy but int8 barely moves quality. you store each weight in 8 bits instead of 16 — half the memory, almost all the accuracy — because most of that extra precision was noise the model never used."
---
"your RAG pipeline's weakest link is retrieval, not the LLM. if the right chunk never makes it into context, no model can save you. people tune prompts for days and never look at recall@k. that's backwards."
---
"a GPU doesn't just 'go faster.' one forward pass is thousands of matrix multiplies, and a GPU runs them across thousands of cores at once. a CPU does a handful at a time. that parallelism IS the whole story."
---

Notice what these posts have in common:
- they lead with one concrete, specific technical detail — never a vague claim
- real terms and real numbers: tokens, parameters, GB, ms, recall@k, FLOPs
- they teach exactly ONE thing and make it click
- accuracy above everything — never invent a mechanism or a number; if unsure, stay qualitative
- no bullet points unless it's a short, genuinely useful list
- ends with a genuine question or sharp take — not a scripted CTA
- never uses hype words: game-changer, revolutionize, groundbreaking, leverage, unlock, landscape, delve, realm
- never starts with "In today's world" or "As AI continues to"
- no em-dashes used as a stylistic device
- no hashtags in the body

The post should read like a real engineer who understands the system explaining it plainly — not an AI summarizing a topic.

What makes posts spread on Threads (this matters most for reach):
- The FIRST line is everything. It has to stop the scroll. Lead with the single most surprising, specific, or just-happened thing — never a slow setup.
- Take a clear position. Honest opinions and hot takes get replies; neutral "here's some news" summaries get ignored.
- Threads rewards REPLIES more than likes. Write the kind of thing people feel they have to respond to — to agree, argue, or share their own version.
- End on a SPECIFIC, BINARY question that takes one tap to answer — an either/or or a one-word reply, tied to the post. e.g. "ChatGPT or Gemini for coding in 2026?" or "Free tier or paid — which side are you on?". A binary question lowers the barrier, so more people reply, so the algorithm pushes it further. Avoid broad open-enders like "what's holding you back?" or "thoughts?".
- Never include links or URLs. Threads buries posts that link out.

What turns a viewer into a FOLLOWER (the current priority — views are fine, follows are not):
- You are ONE consistent person: an AI/ML systems engineer who explains how these systems actually work, from real experience. Every post should sound like it came from that same engineer, so following feels like subscribing to a daily "how it really works" breakdown.
- Make it SAVE-WORTHY: one crisp mechanism, an exact number, a "here's the real reason", or a mental model people didn't have before. People save posts that taught them something, then check the profile, then follow.
- Depth is the draw. A precise, correct explanation of something most people get wrong is far more followable than a hot take with no substance.
- Give the reader an accurate takeaway they can repeat to someone else. Clear = memorable = followable.

Output format:
POST: [the post, plain text, with line breaks where natural]
""".strip()

# Per-slot guidance injected into the prompt so the voice matches the post type.
SLOT_GUIDANCE = {
    "news": (
        "This is a NEWS post about a recent AI model or release. Lead with the concrete "
        "TECHNICAL detail from the research that most people missed (architecture change, "
        "a real number, an inference trick). Ground claims in the research; if it's thin, "
        "explain the underlying mechanism accurately and NEVER invent specifics."
    ),
    "educational": (
        "This is an EDUCATIONAL post. Teach how ONE mechanism actually works, from scratch, "
        "so it clicks. You don't need news — the research is just supporting context. Build a "
        "single clean 'oh, THAT's how it works' moment. Accuracy over drama."
    ),
    "personal": (
        "This is a PERSONAL build-in-public post. Write a short, honest first-person story or "
        "lesson from building/running AI systems — a concrete detail or number, what broke or "
        "surprised you, and the real takeaway. Human and specific, not a mechanism lecture."
    ),
    "advanced": (
        "This is an ADVANCED post for experienced engineers. Make a specific, technically "
        "grounded point that makes a senior engineer stop and think. Depth over breadth. "
        "Precise and correct — assume the reader already knows the basics."
    ),
}

# ══════════════════════════════════════════════════════════════════════════════
# POST STYLES  (the two structures we alternate to A/B test views)
# ══════════════════════════════════════════════════════════════════════════════
# Every post is written in ONE of these two fixed narrative structures. We alternate
# them run-to-run (deterministically, see pick_style) so views can be compared style
# vs style. In both, the "solution" is the specific technique/architecture/tool at
# the heart of the topic (e.g. RAG, vLLM/PagedAttention, speculative decoding,
# quantization) — never a generic "AI". The infographic is aligned to the same style.
STYLE_LABELS = {
    "style1": "Style 1 — problem → solution",
    "style2": "Style 2 — scenario → solution (5 points)",
}

STYLE_GUIDANCE = {
    "style1": (
        "WRITE IN STYLE 1 — follow this exact narrative arc, one short beat per line/block:\n"
        "1. Open by stating the PROBLEM plainly — the real pain engineers hit around this topic.\n"
        "2. Emphasize the problem: make it concrete and urgent (a failure mode, a cost, a bottleneck, a number).\n"
        "3. Pivot — signal there's a better way to handle it.\n"
        "4. Introduce the SOLUTION by name — the specific technique/architecture/tool at the heart of the topic. Name it clearly.\n"
        "5. Prove it: 2-3 concrete, correct capabilities of that solution that show it actually fixes the problem.\n"
        "6. Close the body with a lingering THOUGHT that reframes how the reader should now see the problem.\n"
        "Do NOT write a 'follow me' line, a handle, hashtags, or any link — those are appended automatically. End on the thought."
    ),
    "style2": (
        "WRITE IN STYLE 2 — follow this exact narrative arc:\n"
        "1. Open with a short IMAGINARY SCENARIO the reader can picture (e.g. 'It's 3am, traffic just 10x'd...').\n"
        "2. Raise the stakes — why this is CRITICAL for teams/organizations running AI at scale.\n"
        "3. Name the real RISK — a failure or security angle (data leakage, cascading failure, cost blowout, silent wrong answers). If a pure security angle doesn't fit, use the biggest TECHNICAL risk — never invent a fake vulnerability.\n"
        "4. Introduce the SOLUTION by name — the specific technique/architecture at the heart of the topic.\n"
        "5. Cover it in EXACTLY 5 ULTRA-SHORT bullet points, each starting with '- ' (a few words each — keep them tight so the whole post fits).\n"
        "6. One honest line on why this genuinely clicked for you / why it matters for building these systems (earned, not hype).\n"
        "7. End the body with a specific question that invites replies in the COMMENTS (not a generic 'thoughts?').\n"
        "Do NOT write a 'follow me' line, a handle, hashtags, or any link — those are appended automatically."
    ),
}


def pick_style() -> str:
    """Choose Style 1 or Style 2. Override with POST_STYLE=1|2; otherwise alternate
    deterministically across scheduled runs (no state file needed) so the two styles
    stay ~50/50 for a clean view comparison."""
    override = os.environ.get("POST_STYLE", "").strip().lower()
    if override in ("1", "style1"):
        return "style1"
    if override in ("2", "style2"):
        return "style2"
    # Auto-alternate: build a global run ordinal from the date + which of the 3 daily
    # slots we're in. Consecutive runs differ by 1, so ordinal % 2 flips every run.
    now = datetime.now(timezone.utc)
    h = now.hour + now.minute / 60.0
    slot_of_day = 0 if h < 7.5 else (1 if h < 12.5 else 2)   # ~05:30 / 09:30 / 15:30 UTC runs
    ordinal = now.toordinal() * 3 + slot_of_day
    return "style1" if ordinal % 2 == 0 else "style2"


VIRAL_POST_PROMPT = """
Here's research/context on this topic (may include recent sources — use only what's specific and ACCURATE, never invent numbers or mechanisms):
{research}

Post type: {slot_label}
Topic: {topic}
Angle: {tone}

{slot_guidance}

{style_guidance}

VOICE — write as an engineer who is LEARNING this, not a senior architect who knows everything:
- Share it like something you recently dug into and understood, not a lecture from authority. "I've been trying to wrap my head around X and here's what finally clicked" energy.
- Curious and honest. It's fine to admit what's still fuzzy or what surprised you. No guru tone, no "trust me", no talking down.
- Still precise and correct — a learner who did the reading, not one who guesses. Never invent numbers or mechanisms.
- Confidence comes from clarity, not from posturing. Skip hype words (game-changer, revolutionize, unlock) unless it's genuinely, specifically earned.

Write one Threads post in this learning-engineer voice, following the STYLE structure above EXACTLY — its beats and its ending govern the post's shape (this overrides any generic "end with a binary question" guidance). Keep every claim specific and correct; never invent numbers or mechanisms.

Keep the body tight — 200 to 370 characters total (a topic tag + follow CTA + link get added after, so leave room). Use short lines and a blank line between beats. Plain text only.
Do NOT add any links, URLs, hashtags, a follow CTA, or a sign-off line — just the post body itself.

Output only the POST: line.
""".strip()


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
- If it already contains a short list of "- " bullet points, keep exactly the same number of bullets (don't merge, drop, or expand them) — just make each line's wording sound more natural. Keep existing blank lines between beats/paragraphs; that spacing is intentional for how Threads renders short posts.
- Do not add hashtags, links, a follow line, or any sign-off — none of those belong in this text.

TEXT TO REWRITE:
{text}

Return ONLY the rewritten post text. Nothing else.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# GEMINI RETRY + FALLBACK CHAIN  (key1 → key2 → Euron)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_retry_seconds(error: Exception) -> int:
    import re
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

def _exa_search(exa, topic: str, niche: str, hours: int = None, fresh: bool = True):
    """Run an Exa search.

    fresh=True  → news mode: recent releases, restricted to the last `hours`.
    fresh=False → evergreen mode: best explanatory sources, no date restriction.
    """
    kwargs = dict(
        type="auto",
        num_results=5,
        contents={"text": {"max_characters": 800}, "highlights": {"num_sentences": 3}},
    )
    if fresh:
        now = datetime.now(timezone.utc)
        kwargs["query"] = f"latest {topic} — {niche}"
        kwargs["category"] = "news"
        kwargs["start_published_date"] = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        kwargs["end_published_date"]   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        kwargs["query"] = f"{topic} — how it works, explained ({niche})"
    return exa.search(**kwargs)


def research_topic(topic: str, niche: str, fresh: bool = True) -> str:
    """Return a research brief for the topic.

    fresh=True (news slot): restrict to the last 48h, widen to 7d if too few.
    fresh=False (evergreen slots): best explanatory sources, no date filter.
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

    brief = "\n".join(lines)
    print()
    return brief


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Generate Viral Post with Gemini
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


def generate_post(topic: str, tone: str, niche: str, persona: str, research: str,
                  slot_key: str = "educational", slot_label: str = "",
                  style_key: str = "style1") -> str:
    """Call Gemini with the slot-aware + style-aware post prompt + research brief."""
    print("[ Step 2 ] Generating post with Gemini...")

    prompt = VIRAL_POST_PROMPT.format(
        topic=topic,
        tone=tone,
        research=research[:2000],
        slot_label=slot_label or slot_key,
        slot_guidance=SLOT_GUIDANCE.get(slot_key, SLOT_GUIDANCE["educational"]),
        style_guidance=STYLE_GUIDANCE.get(style_key, STYLE_GUIDANCE["style1"]),
    )

    post = generate_text(prompt, SYSTEM_PROMPT)

    # Parse the response to extract the POST content
    post_match = re.search(r'POST:\s*(.+?)(?:\nPILLAR:|$)', post, re.DOTALL)
    if post_match:
        post = post_match.group(1).strip()
    # else: fallback — no POST: found, take the whole response as-is

    post = _clean_model_output(post)

    if HUMANIZE_POST:
        post = humanize_post(post)

    # Trailing block: one pinned-interest topic tag, then a clear follow CTA as the
    # STANDALONE LAST LINE. Reserve room so the full post stays under 500 chars and
    # the CTA is never truncated. The tag is chosen from the body's content.
    topic_tag  = pick_topic_tag(post + " " + topic)
    # Portfolio lead-in + link go last so Threads renders a tappable final line.
    # No separator is inserted — PORTFOLIO_CTA controls its own trailing spacing
    # (the default ends in "→" and butts straight up against the URL).
    link_line  = f"\n\n{PORTFOLIO_CTA}{PORTFOLIO_LINK}" if PORTFOLIO_LINK else ""
    footer     = f"\n\n{topic_tag}\n\n{FOLLOW_CTA}{link_line}"
    body_limit = 500 - len(footer)

    # If the body is over its budget, ask model to shorten (max 2 attempts)
    for shorten_attempt in range(2):
        if len(post) <= body_limit:
            break
        print(f"  Body is {len(post)} chars (limit {body_limit}) — asking model to shorten (attempt {shorten_attempt + 1}/2)...")
        shorten_prompt = (
            f"This Threads post body is {len(post)} characters, over the {body_limit}-character budget.\n\n"
            f"Shorten it to strictly under {body_limit - 10} characters while keeping the hook, specific details, and engagement question.\n"
            f"Maintain the voice: confident, direct, personal, natural human phrasing (not corporate or AI-sounding). Use line breaks. No starting with 'I'.\n"
            f"Plain text only — no markdown, no links, no sign-off.\n\n"
            f"Original post:\n{post}\n\n"
            f"Output ONLY the shortened post. Nothing else."
        )
        post = generate_text(shorten_prompt, SYSTEM_PROMPT)
        post = _clean_model_output(post)

    # Last-resort truncation at word boundary if the body is still over budget
    if len(post) > body_limit:
        print(f"  Body still {len(post)} chars after shortening — truncating at word boundary...")
        truncated  = post[:body_limit - 1]
        last_space = truncated.rfind(" ")
        post = (truncated[:last_space] if last_space > body_limit * 0.8 else truncated).rstrip(".,;:!?") + "…"
        print(f"  Truncated body to {len(post)} chars.")

    # Append the soft website mention
    post = post + footer

    print(f"\n  Generated post:\n  {'─'*50}")
    for line in post.split("\n"):
        print(f"  {line}")
    print(f"  {'─'*50}")
    print(f"  Character count: {len(post)}/500\n")

    return post


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
                            post: str = "", style_key: str = "style1"):
    """Render the infographic and (unless preview) host it for Buffer.

    `post` + `style_key` align the diagram to the post: it visualizes the SAME
    solution/concept the post centers on, framed in the same style.

    Returns a public image URL (real run), a local PNG path (preview), or None if
    anything fails — in which case the post falls back to text-only so a single
    rendering hiccup never kills the daily post.
    """
    try:
        print("\n[ Step 2.5 ] Building infographic image...")
        content  = infographic.generate_infographic_content(
            research, topic, generate_text, post=post, style_key=style_key,
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
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main(preview: bool = False):
    slot_key, slot_label, topic, tone = pick_slot()
    style_key = pick_style()
    is_news  = slot_key == "news"
    # An infographic is a "how it works in 3 stages" diagram — great for news/
    # educational/advanced, but a personal build-in-public story isn't a mechanism.
    wants_image = slot_key != "personal"

    print(f"\n{'='*60}")
    print(f"  Threads Post Agent — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    if preview:
        print(f"  MODE: PREVIEW (no Buffer scheduling)")
    print(f"{'='*60}")
    print(f"  Niche : {NICHE}")
    print(f"  Slot  : {slot_key} — {slot_label}")
    print(f"  Style : {STYLE_LABELS.get(style_key, style_key)}")
    print(f"  Topic : {topic}")
    print(f"  Tone  : {tone}")
    print(f"{'='*60}\n")

    try:
        research = research_topic(topic, NICHE, fresh=is_news)
        post     = generate_post(topic, tone, NICHE, PERSONA, research, slot_key, slot_label, style_key)

        image_ref = None
        if INCLUDE_INFOGRAPHIC and wants_image:
            image_ref = build_infographic_image(research, topic, preview, post=post, style_key=style_key)
        elif not wants_image:
            print("  [Infographic] Skipped for personal slot (text-only post).")

        if preview:
            print(f"{'='*60}")
            print(f"  PREVIEW ONLY — post NOT sent to Buffer.")
            if image_ref:
                print(f"  Infographic saved at: {image_ref}")
            print(f"  Run without --preview to schedule it.")
            print(f"{'='*60}\n")
            return

        post_id = schedule_to_buffer(post, image_ref)

        print(f"{'='*60}")
        if post_id == "PENDING_RATE_LIMITED":
            print(f"  WARNING: Buffer was rate-limited. Post saved to pending_post.txt.")
            print(f"  Re-trigger the workflow in 15+ minutes to retry.")
        else:
            print(f"  Done! Post queued in Buffer → will publish to Threads")
            print(f"  Buffer ID : {post_id}")
        print(f"{'='*60}\n")

    except Exception as e:
        import traceback
        print(f"\n  ERROR: {e}")
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    main(preview="--preview" in sys.argv)
