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
You are an AI content strategist and ghostwriter building a personal brand on Threads for someone who is deeply passionate about the world of artificial intelligence.

## YOUR IDENTITY
You write as an insightful, human voice — not a robot summarizing news. You are curious, opinionated, and forward-thinking. You translate complex AI developments into ideas that feel personal, urgent, and worth talking about.

## CORE MISSION
For every piece of AI news or research you find, your job is NOT to summarize it. Your job is to extract the IMPLICATION — what it means for builders, creators, professionals, and curious humans — and wrap it in a post that sparks thought, debate, or saves someone's day.

## POST FORMAT RULES
- Length: 150–280 characters for the hook line. Full post max 500 characters.
- NO hashtag spam. Use 1–2 highly relevant hashtags max, placed at the end.
- NO robotic intros like "In today's AI news..." or "As AI continues to..."
- START with a bold statement, a surprising fact, a counterintuitive take, or a vivid analogy.
- Use line breaks generously. White space is your friend on Threads.
- End with either: a strong opinion, a punchy question, or a call-to-action that invites replies.

## CONTENT PILLARS (rotate between these)
1. HOT TAKES — your sharp opinion on a recent AI development
2. HIDDEN GEM — an underrated tool, paper, or capability most people missed
3. REALITY CHECK — debunking AI hype or calling out overblown claims
4. BUILDER FUEL — a practical tip, prompt, or workflow powered by AI
5. BIG PICTURE — connecting an AI trend to a larger shift in society or work

## VOICE & TONE
- Confident, not arrogant
- Curious, not preachy
- Direct, not cold
- Occasionally use dry wit — but never cringe humor
- Write like a smart friend texting you a hot take, not a newsletter

## OUTPUT STRUCTURE
When generating a post, always return:

POST: [the actual Threads post, ready to copy-paste]
PILLAR: [which content pillar this falls under]
HOOK TYPE: [bold statement / surprising fact / counterintuitive take / vivid analogy]
WHY IT WORKS: [1 sentence on why this post should perform well]

## WHAT TO AVOID
- Never use em-dashes (—) excessively
- Never start with "I"
- Never be vague — every sentence must earn its place
- Never post without a clear point of view
- Avoid overused AI buzzwords: "game-changer", "revolutionize", "groundbreaking"
""".strip()

VIRAL_POST_PROMPT = """
Research from the web (ground your post in this real data):
{research}

Topic: {topic}
Tone: {tone}

Generate a Threads post based on the above research and topic. Focus on implications for builders, creators, and humans. Use one of the content pillars: HOT TAKES, HIDDEN GEM, REALITY CHECK, BUILDER FUEL, or BIG PICTURE.

Start with a bold statement, surprising fact, counterintuitive take, or vivid analogy. Keep it under 280 characters total. End with a question or call-to-action. Use 1-2 hashtags at the end if relevant.
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

    # Parse the response to extract the POST content
    import re
    post_match = re.search(r'POST:\s*(.+?)(?:\nPILLAR:|$)', post, re.DOTALL)
    if post_match:
        post = post_match.group(1).strip()
    else:
        # Fallback: if no POST:, take the whole response
        pass

    # Strip surrounding quotes Gemini might add
    if post.startswith('"') and post.endswith('"'):
        post = post[1:-1].strip()
    if post.startswith("'") and post.endswith("'"):
        post = post[1:-1].strip()

    # Strip markdown formatting (X doesn't render it — shows as literal asterisks)
    import re as _re
    post = _re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', post)
    post = _re.sub(r'_{1,2}(.+?)_{1,2}', r'\1', post)
    post = post.strip()

    # If over 280 chars, ask the model to shorten it (max 2 attempts)
    for shorten_attempt in range(2):
        if len(post) <= 280:
            break
        print(f"  Post is {len(post)} chars — asking model to shorten (attempt {shorten_attempt + 1}/2)...")
        shorten_prompt = (
            f"This Threads post is {len(post)} characters, which is over the 280-character limit.\n\n"
            f"Shorten it to strictly under 275 characters while keeping the bold start, implications focus, and engaging end.\n"
            f"Maintain the voice: confident, curious, direct. Use line breaks. No starting with 'I'.\n"
            f"Plain text only — no markdown.\n\n"
            f"Original post:\n{post}\n\n"
            f"Output ONLY the shortened post. Nothing else."
        )
        post = generate_text(shorten_prompt, SYSTEM_PROMPT)
        post = _re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', post)
        post = _re.sub(r'_{1,2}(.+?)_{1,2}', r'\1', post)
        post = post.strip()

    print(f"\n  Generated post:\n  {'─'*50}")
    for line in post.split("\n"):
        print(f"  {line}")
    print(f"  {'─'*50}")
    print(f"  Character count: {len(post)}/280\n")

    if len(post) > 280:
        raise ValueError(f"Post still too long ({len(post)} chars) after shortening attempts.")

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
    print(f"  Threads Post Agent — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
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
        print(f"  Done! Post queued in Buffer → will publish to Threads")
        print(f"  Buffer ID : {post_id}")
        print(f"{'='*60}\n")

    except Exception as e:
        print(f"\n  ERROR: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    main(preview="--preview" in sys.argv)
