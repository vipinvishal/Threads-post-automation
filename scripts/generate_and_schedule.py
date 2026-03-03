#!/usr/bin/env python3
"""
X Post Agent
Pipeline: Exa (research) → Gemini (generate viral post) → Buffer (schedule to X)

Run locally : python scripts/generate_and_schedule.py
GitHub Actions triggers this automatically every day at 9 AM IST.
"""

import os
import json
import random
import time
import requests
from datetime import datetime, timezone, timedelta
from exa_py import Exa
from google import genai
from google.genai import types
from dotenv import load_dotenv

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
MAX_RETRIES            = 4
RETRY_BASE_SECONDS     = 15

# ── Load topics config ────────────────────────────────────────────────────────
_script_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_script_dir, "topics.json"), "r") as f:
    _config = json.load(f)

NICHE   = _config["niche"]
PERSONA = _config["persona"]
TOPICS  = _config["topics"]
TONES   = _config["tones"]


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
You are a ghost-writer for a technical AI founder on X (Twitter).
You write like someone who has actually shipped AI products — raw, specific, opinionated.
You know that specificity beats inspiration, one sharp sentence beats a paragraph, and
the audience (AI founders, ML engineers, CTOs) can spot generic AI hype instantly.
""".strip()

VIRAL_POST_PROMPT = """
You are writing for an X account in the AI/tech space.
The audience is: AI founders, ML engineers, technical builders, AI enthusiasts, CTOs.
The goal: maximum views, comments, likes — leading to audience monetization.

━━━ INPUT ━━━
Niche   : {niche}
Persona : {persona}
Topic   : {topic}
Tone    : {tone}

Voice rule: Every post must sound like it came from someone who was personally in the room
when this happened — not someone who read about it. Use "I", "we", "my team", "I shipped",
"I broke", "I learned". First-person always. No exceptions.

Research from the web (ground your post in this real data):
{research}

━━━ VIRAL FRAMEWORKS — pick the best one for this topic ━━━

Framework A — The Contrarian AI Take:
  [Claim that goes against popular AI opinion]
  [Specific technical reason why]
  [What most people miss]
  [Question that makes AI builders want to reply]

Framework B — The Builder War Story:
  [What I tried / built / shipped]
  [What actually happened — specific numbers or outcome]
  [The uncomfortable lesson]
  [Question inviting others to share their experience]

Framework C — The Hype vs Reality:
  [The thing everyone believes about AI]
  [What actually happens in production]
  [The specific gap nobody talks about]
  [Sharp closing question or statement]

Framework D — The Prediction / Hot Take:
  [Bold claim about where AI is going]
  [3 specific signals that support it]
  [Who this affects and how]
  [Question that sparks debate]

━━━ RULES ━━━
✓ Max 280 characters TOTAL
✓ Line 1 MUST hook — contrarian, surprising, or provocative
✓ Every line break must earn its place — no filler lines
✓ Use specific technical terms (LLM, RAG, fine-tuning, inference, etc.) — the audience is technical
✓ End with a question that a senior ML engineer or AI founder would genuinely want to answer
✓ Sound like a builder who has actually shipped — not a tech journalist
✗ NO hashtags
✗ NO generic emojis like 🚀🔥💡
✗ NO hype language ("game-changing", "revolutionary", "the future is here")
✗ NO vague statements — every claim must be specific
✗ NEVER cite external sources, tools, or companies as proof (no "See Devin", no "According to OpenAI") — all credibility must come from first-person experience or direct observation
✗ NEVER write from a journalist or analyst perspective — always write as someone who personally built, shipped, broke, or fixed the thing
✗ NEVER use corporate language ("this quarter", "leverage", "utilize", "use case", "ROI" as a standalone buzzword)
✗ NEVER present 3 competing ideas in one post — pick ONE insight and go deep on it
✗ NO bold/italic markdown — plain text only

━━━ OUTPUT ━━━
ONLY the post text. No quotes. No explanation. No preamble.
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
            json={"model": "gemini-2.0-flash", "messages": messages},
            timeout=90,
        )
        if resp.status_code == 429:
            wait = 20 * attempt
            print(f"  [Euron] 429 rate limit, attempt {attempt}/3. Waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
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

def research_topic(topic: str, niche: str) -> str:
    """Find 5 recent high-quality articles on the topic and return a research brief."""
    print("\n[ Step 1 ] Researching topic with Exa...")

    exa = Exa(api_key=EXA_API_KEY)
    results = exa.search(
        query=f"{topic} {niche} insights trends 2025",
        type="auto",
        num_results=5,
        start_published_date="2025-01-01",
        contents={
            "text": {"max_characters": 800},
            "highlights": {"num_sentences": 3},
        },
    )

    lines = []
    for i, result in enumerate(results.results, 1):
        title      = result.title or "Untitled"
        url        = result.url
        text       = (result.text or "")[:600].strip()
        highlights = result.highlights or []

        lines.append(f"Source {i}: {title}")
        lines.append(f"URL: {url}")
        if highlights:
            lines.append(f"Key insight: {highlights[0]}")
        if text:
            lines.append(f"Context: {text[:300]}...")
        lines.append("")

    brief = "\n".join(lines)
    print(f"  Found {len(results.results)} sources.\n")
    return brief


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Generate Viral Post with Gemini
# ══════════════════════════════════════════════════════════════════════════════

def generate_post(topic: str, tone: str, niche: str, persona: str, research: str) -> str:
    """Call Gemini with the viral post prompt + research brief."""
    print("[ Step 2 ] Generating post with Gemini...")

    prompt = VIRAL_POST_PROMPT.format(
        niche=niche,
        persona=persona,
        topic=topic,
        tone=tone,
        research=research[:2000],
    )

    post = generate_text(prompt, SYSTEM_PROMPT)

    # Strip surrounding quotes Gemini might add
    if post.startswith('"') and post.endswith('"'):
        post = post[1:-1].strip()
    if post.startswith("'") and post.endswith("'"):
        post = post[1:-1].strip()

    # Strip markdown formatting (X doesn't render it — shows as literal asterisks)
    import re as _re
    post = _re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', post)  # **bold**, *italic*, ***both***
    post = _re.sub(r'_{1,2}(.+?)_{1,2}', r'\1', post)     # _italic_, __bold__
    post = post.strip()

    print(f"\n  Generated post:\n  {'─'*50}")
    for line in post.split("\n"):
        print(f"  {line}")
    print(f"  {'─'*50}")
    print(f"  Character count: {len(post)}/280\n")

    if len(post) > 280:
        raise ValueError(f"Post too long ({len(post)} chars). Aborting to avoid truncation on X.")

    return post


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Schedule to Buffer
# ══════════════════════════════════════════════════════════════════════════════

def schedule_to_buffer(post_text: str) -> str:
    """Push the post to Buffer via GraphQL. Schedules 5 minutes from now."""
    print("[ Step 3 ] Scheduling to Buffer...")

    due_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    mutation = """
    mutation CreatePost($text: String!, $channelId: ChannelId!, $dueAt: DateTime) {
      createPost(input: {
        text: $text,
        channelId: $channelId,
        schedulingType: automatic,
        mode: customScheduled,
        dueAt: $dueAt
      }) {
        ... on PostActionSuccess {
          post {
            id
            text
          }
        }
        ... on MutationError {
          message
        }
      }
    }
    """

    response = requests.post(
        "https://api.buffer.com",
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
            },
        },
        timeout=15,
    )

    data = response.json()

    if "errors" in data:
        raise RuntimeError(f"Buffer API error: {data['errors']}")

    result = data.get("data", {}).get("createPost", {})
    if "message" in result:
        raise RuntimeError(f"Buffer mutation error: {result['message']}")

    post_id = result.get("post", {}).get("id", "unknown")
    print(f"  Scheduled! Buffer Post ID: {post_id}")
    print(f"  Publish time : {due_at}\n")
    return post_id


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main(preview: bool = False):
    topic = random.choice(TOPICS)
    tone  = random.choice(TONES)

    print(f"\n{'='*60}")
    print(f"  X Post Agent — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    if preview:
        print(f"  MODE: PREVIEW (no Buffer scheduling)")
    print(f"{'='*60}")
    print(f"  Niche : {NICHE}")
    print(f"  Topic : {topic}")
    print(f"  Tone  : {tone}")
    print(f"{'='*60}\n")

    try:
        research = research_topic(topic, NICHE)
        post     = generate_post(topic, tone, NICHE, PERSONA, research)

        if preview:
            print(f"{'='*60}")
            print(f"  PREVIEW ONLY — post NOT sent to Buffer.")
            print(f"  Run without --preview to schedule it.")
            print(f"{'='*60}\n")
            return

        post_id = schedule_to_buffer(post)

        print(f"{'='*60}")
        print(f"  Done! Post queued in Buffer → will publish to X")
        print(f"  Buffer ID : {post_id}")
        print(f"{'='*60}\n")

    except Exception as e:
        print(f"\n  ERROR: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    main(preview="--preview" in sys.argv)
