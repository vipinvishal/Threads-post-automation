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

BRAND_SIGNOFFS = [
    f"this is the kind of thing we obsess over at {BRAND_NAME}",
    f"building stuff like this at {BRAND_NAME}",
    f"we test this kind of thing daily at {BRAND_NAME}",
    f"this is exactly what we do at {BRAND_NAME}",
    f"that's the whole reason i started {BRAND_NAME}",
]
FOLLOW_SIGNOFFS = [
    "i break down a new AI tool here every day — follow if that's useful",
    "follow along — i post what's actually worth it in AI, daily",
    "i test AI tools daily and post the verdict here. follow if you want in",
    "new AI find every day on this page. follow so you don't miss it",
    "i do this breakdown daily here — follow along",
]
# Weighted toward follow-value lines since growing followers is the current goal.
FOLLOW_SIGNOFF_RATIO = float(os.environ.get("FOLLOW_SIGNOFF_RATIO", "0.6"))


def pick_signoff() -> str:
    """Alternate between a follow-value line and a brand mention."""
    pool = FOLLOW_SIGNOFFS if random.random() < FOLLOW_SIGNOFF_RATIO else BRAND_SIGNOFFS
    return random.choice(pool)

# ── Topic tag ────────────────────────────────────────────────────────────────────
# Threads turns the FIRST hashtag in a post into a native topic tag (only one is
# allowed), and Meta confirms tagged posts get more views. We append exactly one
# relevant tag, matched to the post's content. Keyword → tag, first match wins.
TOPIC_TAG_RULES = [
    (("chatgpt", "gpt-4", "gpt4", "openai", "gpt-5", "gpt5"), "#ChatGPT"),
    (("claude", "anthropic"),                                  "#Claude"),
    (("gemini", "notebooklm", "google ai"),                    "#Gemini"),
    (("perplexity",),                                          "#Perplexity"),
    (("cursor", "copilot", "replit", "coding", "code"),        "#AICoding"),
    (("midjourney", "dall-e", "dalle", "ideogram", "image"),   "#AIArt"),
    (("money", "earn", "income", "side hustle", "$", "revenue", "monetize"), "#MakeMoneyWithAI"),
    (("productivity", "workflow", "automate", "automation", "hours"),        "#AIProductivity"),
]
DEFAULT_TOPIC_TAG = os.environ.get("DEFAULT_TOPIC_TAG", "#AI")


def pick_topic_tag(text: str) -> str:
    """Choose one relevant topic tag based on the post's content."""
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

NICHE   = _config["niche"]
PERSONA = _config["persona"]
TOPICS  = _config["topics"]
TONES   = _config["tones"]


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
You write Threads posts for someone who's deep into AI tools and shares what they actually find — casually, like texting a friend, not publishing an article.

The voice is relaxed and real. Short sentences. Personal. A little unpolished on purpose. Like someone who just tried something and had to tell people about it.

Here are examples of the exact style to match:

---
"nobody told me perplexity was this good. i've barely touched google in 2 weeks"
---
"ok so i gave Claude a 40 page PDF and asked it to find every contradiction in the document. it found 11. my lawyer found 2. i don't know how to feel about this"
---
"spent $0 last month on tools i used to pay $200/month for. free tier of claude, perplexity, and gamma do literally everything i need"
---
"the people winning with AI right now aren't the ones with the best prompts. they're the ones who built systems around it. big difference"
---
"chatgpt vs claude honest take after using both daily for 6 months: claude for writing and thinking, chatgpt for quick lookups and code. that's it"
---
"tried to explain what i do to my parents. 'i use AI to do things that used to take a team of people.' my mom said 'so you'll be unemployed soon?' she's not entirely wrong lol"
---
"free AI tools that are genuinely good right now:
- claude.ai (free tier is great)
- perplexity (better than google for research)
- gamma (decks in minutes)
- notebooklm (reads your docs for you)

been paying $0 for 3 weeks. no difference in my output"
---
"hot take: most people using AI tools are just doing fancy copy paste. the ones making real money have automated entire workflows. it's a totally different game"
---

Notice what these posts have in common:
- they sound like a real person discovered something and had to share it
- specific tool names, specific numbers, specific reactions
- no bullet points with dashes unless it's a short practical list
- lowercase feels more casual and authentic
- ends with a genuine reaction, question, or offhand comment — not a scripted CTA
- never uses words like: game-changer, revolutionize, groundbreaking, leverage, landscape, delve, realm
- never starts with "In today's world" or "As AI continues to"
- no em-dashes used as a stylistic device
- no hashtags at all, or at most one simple one like #AI at the very end

The post should feel like something a real person actually posted — not something that was generated.

What makes posts spread on Threads (this matters most for reach):
- The FIRST line is everything. It has to stop the scroll. Lead with the single most surprising, specific, or just-happened thing — never a slow setup or "so I was thinking about..."
- Take a clear position. Honest opinions and hot takes get replies; neutral "here's some news" summaries get ignored. Say what most people are missing or getting wrong.
- Threads rewards REPLIES more than likes. Write the kind of thing people feel they have to respond to — to agree, argue, or share their own version.
- End on a real, specific question or open invitation tied to the post — not a generic "thoughts?" or "what do you think?". Make it easy and tempting to answer.
- Never include links or URLs. Threads buries posts that link out.

What turns a viewer into a FOLLOWER (the current priority — views are fine, follows are not):
- You are ONE consistent person: someone who tests AI tools every day and reports what's actually worth it, calls out hype, and helps normal people save time and make money. Every post should sound like it came from that same person, so following feels like subscribing to that daily verdict.
- Make it SAVE-WORTHY when the topic allows: a concrete tool name + what it replaces, an exact number, a tiny step-by-step, or a "free vs paid" verdict. People save useful posts, then check the profile, then follow. A clever take alone earns a like, not a follow.
- Give specifics people can act on TODAY, not vague encouragement. Specific = memorable = followable.

Output format:
POST: [the post, plain text, with line breaks where natural]
""".strip()

VIRAL_POST_PROMPT = """
Here's FRESH research from the last 48 hours on this topic — these are recent news, announcements, and releases. Pull out anything specific and interesting (tool names, numbers, facts, surprising details). React like someone who just read the news today:
{research}

Topic: {topic}
Vibe: {tone}

Write one Threads post in the casual human voice from your instructions. Build it in three beats:
- HOOK (line 1): open with the most surprising, specific, RECENT thing from the research — a real tool name, a real number, a just-happened announcement. Make someone go "wait, that just dropped?" No slow setup.
- TAKE: give your honest reaction or opinion on it. Say what most people are missing or getting wrong. Have a spine.
- ENGAGE (last line): end with a specific question or open invitation that makes people want to reply — their experience, their pick, agree or disagree. Tie it to the post, never a generic "thoughts?".

The post should feel like a real person reacting to news they just saw — not an AI summarizing a topic.

Keep it between 180 and 380 characters (a short sign-off gets added after, so leave room). Plain text only.
Do NOT add any links, URLs, hashtags, or a sign-off line — just the post itself.

Output only the POST: line.
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

def _exa_search(exa, topic: str, niche: str, hours: int):
    """Run an Exa news search restricted to the last `hours`."""
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return exa.search(
        query=f"latest {topic} news announcement update — {niche}",
        type="auto",
        category="news",
        num_results=5,
        start_published_date=start,
        end_published_date=end,
        contents={
            "text": {"max_characters": 800},
            "highlights": {"num_sentences": 3},
        },
    )


def research_topic(topic: str, niche: str) -> str:
    """Find recent (last 48h) high-quality articles on the topic and return a research brief."""
    print("\n[ Step 1 ] Researching topic with Exa...")

    exa = Exa(api_key=EXA_API_KEY)
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

    # Soft brand promotion + one topic tag for reach. Reserve room for both so the
    # full post (body + sign-off + tag) stays under Threads' 500-char limit and
    # nothing is truncated away. The tag is chosen from the body's content.
    signoff    = pick_signoff()
    topic_tag  = pick_topic_tag(post + " " + topic)
    footer     = f"\n\n{signoff}\n\n{topic_tag}"
    body_limit = 500 - len(footer)

    # If the body is over its budget, ask model to shorten (max 2 attempts)
    for shorten_attempt in range(2):
        if len(post) <= body_limit:
            break
        print(f"  Body is {len(post)} chars (limit {body_limit}) — asking model to shorten (attempt {shorten_attempt + 1}/2)...")
        shorten_prompt = (
            f"This Threads post body is {len(post)} characters, over the {body_limit}-character budget.\n\n"
            f"Shorten it to strictly under {body_limit - 10} characters while keeping the hook, specific details, and engagement question.\n"
            f"Maintain the voice: confident, direct, personal. Use line breaks. No starting with 'I'.\n"
            f"Plain text only — no markdown, no links, no sign-off.\n\n"
            f"Original post:\n{post}\n\n"
            f"Output ONLY the shortened post. Nothing else."
        )
        post = generate_text(shorten_prompt, SYSTEM_PROMPT)
        post = _re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', post)
        post = _re.sub(r'_{1,2}(.+?)_{1,2}', r'\1', post)
        post = post.strip()

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

def build_infographic_image(research: str, topic: str, preview: bool):
    """Render the infographic and (unless preview) host it for Buffer.

    Returns a public image URL (real run), a local PNG path (preview), or None if
    anything fails — in which case the post falls back to text-only so a single
    rendering hiccup never kills the daily post.
    """
    try:
        print("\n[ Step 2.5 ] Building infographic image...")
        content  = infographic.generate_infographic_content(research, topic, generate_text)
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

        image_ref = None
        if INCLUDE_INFOGRAPHIC:
            image_ref = build_infographic_image(research, topic, preview)

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
