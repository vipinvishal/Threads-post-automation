# Threads Post Automation

An AI agent that researches a topic, writes a Threads post in the day's locked format, renders a matching hand-drawn infographic, runs it through a set of pipeline-level quality gates, and publishes to Threads automatically — **3 times a day, fully automated via GitHub Actions.**

**No VPS needed. No manual work.**

---

## What It Posts

The voice behind [@vipinailabs](https://www.threads.net/@vipinailabs): a builder who tests AI tools hands-on and reports real costs and real results, specifically for Indian developers deciding whether something is worth their time or money. LLM internals and agentic AI systems — mechanisms, not hype.

Examples of the content direction:
- *"the kv cache is why chat feels instant — not magic, just cached math. the model saves the attention keys/values for every token it already processed, so each new token is one step of work."*
- *"quantization sounds like it should wreck a model. mostly it doesn't — int4 stores weights in 4 bits instead of 16, and the accuracy loss is tiny because the weights are noisy anyway."*
- *"₹1,850/month to self-host a 7B model on RunPod. the OpenAI API equivalent for the same traffic came out cheaper below 2M tokens/day."*

---

## How It Works

```
GitHub Actions (11 AM / 3 PM / 9 PM IST)
        ↓
Day of week (IST) LOCKS the format for every run that day — see Format Rotation
        ↓
Exa — neural web research
  • hot_take: last-48h fresh releases   • quote_react: a real recent claim to react to
  • other formats: evergreen explanatory sources
        ↓
Gemini — writes the post as JSON (hook, body, cta_included, tag, image_template,
  numeric_claims, reply_seed) in the @vipinailabs voice
  └─ fallback: Gemini key #2 → Euron API
        ↓
Humanize pass — rewrites the phrasing so it reads like a person, not an AI
        ↓
Quality gates (pipeline code, not the prompt — see Quality Gates below)
  • fact-check numeric_claims against the research brief, auto-soften if unsupported
  • repetition check against the last 10 posts — regenerate the hook/closing if too close
  • CTA cap — at most 1-in-8 posts gets the follow CTA + link, day-lock AND'd with a
    rolling-history check
  • image-template variety — force a swap if the same template was overused recently
        ↓
Infographic (skipped when image_template is "none")
  Gemini → template-specific JSON → Jinja2 → Playwright → 1800px PNG → hosted on imgbb
        ↓
Buffer — schedules post + attached infographic to Threads
        ↓
scripts/post_history.json — the run's record is appended and committed back to the
  repo, so the quality gates above have real history on the next run
```

Every post ends with one **topic tag**; the follow CTA + portfolio link only appear on the rare gate-approved post (see [CTA Cap](#quality-gates)):

```
#AI
```
```
speculative decoding is a neat trick.
...
how many tokens do you draft ahead?

#AI

follow @vipinailabs for daily dose of information

See what I've been building →https://vipin-vishal.onrender.com/
```

---

## Posting Schedule

| Time (IST) | UTC | Cron |
|---|---|---|
| 11:00 AM | 05:30 | `30 5 * * *` |
| 3:00 PM | 09:30 | `30 9 * * *` |
| 9:00 PM | 15:30 | `30 15 * * *` |

3 runs a day, 7 days a week — Sunday's runs are a no-op (see below), so no post actually goes out that day.

---

## Format Rotation

The day of the week (IST) **locks** the format for every run that day — the model never picks its own format. Defined in `DAY_ROTATION` in [`scripts/generate_and_schedule.py`](scripts/generate_and_schedule.py).

| Day | Format | Image template options | CTA-eligible |
|---|---|---|---|
| Mon | `mechanism_explainer` | three_stage_flow / before_after | No |
| Tue | `hot_take` | single_stat_hero | No |
| Wed | `build_log` | annotated_screenshot / none | No |
| Thu | `india_cost` | single_stat_hero | No |
| Fri | `mechanism_explainer` | before_after / timeline | **Yes — the one CTA slot** |
| Sat | `quote_react` | none | No |
| Sun | — | — | **Off — reply-only day, no post generated** |

- **mechanism_explainer** — a genuinely sequential concept gets `three_stage_flow`/`timeline`; a trade-off/comparison gets `before_after`. Topics come from `format_topics.mechanism_explainer` in [`scripts/topics.json`](scripts/topics.json).
- **hot_take** — one real stat + one sentence of context + one question, sourced from last-48h research.
- **build_log** — first-person, something that actually broke or surprised you today. Rendered as a stylized terminal/log window with hand-drawn callouts.
- **india_cost** — must ground the post in a real ₹ figure or a named Indian cloud/hardware context (RunPod, AWS Mumbai, a consumer GPU price).
- **quote_react** — Exa sources a real, recent AI opinion/claim; the model reacts to it (agree, disagree, add a missing angle). No image, no CTA.

Monday and Friday share `mechanism_explainer` but get **different** image-template pools on purpose, so the two mechanism-explainer days don't default to looking the same.

---

## Quality Gates

The system prompt (`POST_SYSTEM_PROMPT` in `scripts/generate_and_schedule.py`) sets the voice and the JSON output contract. Everything below runs **in pipeline code around the model call**, not as prompt instructions the model could drift away from:

| Gate | What it does |
|---|---|
| **Fact-check** | Cross-checks every `numeric_claims` entry against the same Exa research brief the writer saw. If a number isn't supported, one more Gemini call rewrites just that number into a qualitative statement. Skipped entirely if every claim already matches. |
| **Repetition check** | Fuzzy-compares the new hook/closing line against the last 10 posts (`scripts/post_history.json`), plus a hard check on 3 named recurring patterns ("it's not X, it's Y", etc.). On a match, asks the model to rewrite just the opening/closing — up to 2 attempts, then posts anyway with a warning rather than blocking the scheduled run. |
| **CTA cap** | `cta_included` only ends up `true` if the model wanted it AND today is the CTA-eligible day AND none of the last 7 posts already had one. This is what keeps the real ratio near ~1-in-8 even across 3 runs/day on the Friday slot. |
| **Template variety** | If the model's chosen `image_template` has been used more than 3 times in the last 8 posts, swaps to whichever allowed option for that day was used least recently. Skipped when a format only has one valid option (e.g. `quote_react` → always `none`). |

All four read `scripts/post_history.json` (a rolling 60-entry record) and the CTA/repetition/template gates write a new entry to it after a successful real (non-preview) run. Because GitHub Actions runs are stateless containers, the workflow commits that file back to the repo after each run so the *next* run has real history to check against.

---

## Tech Stack

| Tool | Purpose |
|---|---|
| **GitHub Actions** | Scheduling (replaces VPS/cron) + commits `post_history.json` back after each run |
| **Exa** | Real-time neural web research + claim-sourcing for `quote_react` |
| **Google Gemini** | Post JSON, humanize pass, fact-check pass, infographic-content generation (dual-key quota rotation) |
| **Euron API** | Last-resort fallback when all Gemini keys are exhausted |
| **Playwright + Jinja2** | Render one of 5 infographic templates (HTML) → PNG |
| **imgbb** | Hosts the PNG so Buffer can attach it |
| **Buffer** | Schedules and publishes post + image to Threads |

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/vipinvishal/Threads-post-automation.git
cd Threads-post-automation
```

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium # needed for infographic rendering
```

### 3. Set up your `.env` file

```bash
cp .env.example .env
```

Fill in your API keys (see [Configuration](#configuration) below).

### 4. Test locally

```bash
# Preview — generates post + infographic, does NOT send to Buffer or touch history
python scripts/generate_and_schedule.py --preview

# Full run — research → post → quality gates → infographic → schedule to Buffer
python scripts/generate_and_schedule.py
```

---

## Configuration

### Required secrets (`.env` / GitHub Actions secrets)

| Variable | Where to get it |
|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `EXA_API_KEY` | [exa.ai](https://exa.ai) |
| `BUFFER_API_KEY` | buffer.com → Settings → API |
| `BUFFER_CHANNEL_ID` | Run `python scripts/get_buffer_channel.py` |
| `IMGBB_API_KEY` | [api.imgbb.com](https://api.imgbb.com) — free tier is enough |

> If `IMGBB_API_KEY` is unset (or rendering fails), the run falls back to a **text-only** post. A render hiccup never kills the daily post.

### Optional secrets

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY_2` | Second Gemini key for quota fallback |
| `EURON_API_KEY` | Euron last-resort fallback |

### Optional env vars / repo variables

| Variable | Default | Effect |
|---|---|---|
| `HUMANIZE_POST` | `1` | Set `0` to skip the humanize rewrite pass and post Gemini's raw phrasing |
| `INCLUDE_INFOGRAPHIC` | `1` | Set `0` for text-only posts |
| `INFOGRAPHIC_HANDLE` | `@vipinailabs` | Handle shown on the infographic |
| `PORTFOLIO_URL` | `vipin-vishal.onrender.com` | Text in the infographic footer + the clickable link on the rare CTA post |
| `PORTFOLIO_CTA` | `See what I've been building →` | Lead-in before the portfolio link |
| `FOLLOW_CTA` | `follow @vipinailabs for daily dose of information` | The follow ask — use the **real** handle |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Primary model |
| `NEWS_WINDOW_HOURS` | `48` | Fresh-news window for `hot_take` / `quote_react` |
| `DEFAULT_TOPIC_TAG` | `#AI` | Fallback topic tag, used when the model's own `tag` doesn't resolve to a pinned interest |

> **Handle must be real.** Threads renders a tappable `@mention` only when the handle resolves — a wrong one silently degrades to plain text.
>
> **The CTA/link are already rare by design** — see [Quality Gates](#quality-gates) — so there's no separate on/off toggle for them beyond `FOLLOW_CTA`/`PORTFOLIO_URL` content.

### Finding your Buffer Channel ID

```bash
# Make sure BUFFER_API_KEY is in .env first
python scripts/get_buffer_channel.py
```

Copy the ID for your Threads channel and set it as `BUFFER_CHANNEL_ID`.

---

## GitHub Actions Setup

1. **Add secrets** — Settings → Secrets and variables → Actions → Secrets:
   `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, `EURON_API_KEY`, `EXA_API_KEY`,
   `BUFFER_API_KEY`, `BUFFER_CHANNEL_ID`, `IMGBB_API_KEY`.
2. **(Optional) add variables** — `INFOGRAPHIC_HANDLE`, `PORTFOLIO_URL`, `PORTFOLIO_CTA`, `FOLLOW_CTA`.
3. The workflow ([`.github/workflows/daily_post.yml`](.github/workflows/daily_post.yml)) runs at **11 AM, 3 PM, 9 PM IST**, and needs `permissions: contents: write` (already set) so it can commit `scripts/post_history.json` back after each run. Manual trigger: **Actions → Daily Threads Post → Run workflow**.

---

## Customizing Content

Edit [`scripts/topics.json`](scripts/topics.json):

| Key | What it controls |
|---|---|
| `niche` | The content category fed to Exa for research |
| `format_topics` | A topic seed list per format (`mechanism_explainer`, `hot_take`, `build_log`, `india_cost`) — `quote_react` has none, it's sourced live from a real claim |

The account voice + JSON output contract live in `POST_SYSTEM_PROMPT`, the day → format mapping lives in `DAY_ROTATION`, and the infographic templates/fonts/portrait live in [`renderer/`](renderer/) — one content schema per template in [`scripts/infographic_templates.py`](scripts/infographic_templates.py).

---

## Project Structure

```
├── scripts/
│   ├── generate_and_schedule.py   # main pipeline: rotation, post JSON, quality gates, schedule
│   ├── infographic.py             # infographic content-gen dispatcher + imgbb upload
│   ├── infographic_templates.py   # one schema/prompt/coercion per image template
│   ├── topics.json                # niche, format_topics
│   ├── post_history.json          # rolling record the quality gates read/write (git-tracked)
│   └── get_buffer_channel.py      # one-time helper to find Buffer channel ID
├── renderer/
│   ├── render.py                  # Jinja2 (5-template registry) → Playwright → 1800px PNG
│   ├── templates/
│   │   ├── three_stage_flow.html.j2
│   │   ├── single_stat_hero.html.j2
│   │   ├── before_after.html.j2
│   │   ├── annotated_screenshot.html.j2
│   │   ├── timeline.html.j2
│   │   └── partials/              # shared spiral binding, footer, palette CSS
│   └── fonts/  data/  icons.py
├── .github/workflows/daily_post.yml
├── .env.example
├── requirements.txt
└── .gitignore
```

---

## Fallback Chain

```
Gemini key #1
    → Gemini key #2   (if GEMINI_API_KEY_2 is set)
        → Euron API   (if EURON_API_KEY is set)
```

- **Infographic fallback** — any render/upload failure or missing `IMGBB_API_KEY` → clean text-only post.
- **Buffer rate limits** — retried with backoff; if still limited, the post is saved to `pending_post.txt` to re-run later.
- **Quality-gate soft-fail** — repetition/fact-check retries are bounded (max 2 attempts); if still flagged, the pipeline posts anyway with a warning rather than skipping the scheduled post.

No manual intervention needed on quota exhaustion.

---

## License

MIT
