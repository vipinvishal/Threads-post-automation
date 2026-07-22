# Threads Post Automation

An AI agent that researches an AI-systems topic, writes a technical Threads post in one of two fixed styles, renders a matching hand-drawn infographic, and publishes both to Threads automatically — **3 times a day, fully automated via GitHub Actions.**

**No VPS needed. No manual work.**

---

## What It Posts

Pure AI-systems education, in a **learning-engineer voice** — a curious engineer sharing what they just figured out, not an architect lecturing. How LLMs, transformers, RAG, agents, GPUs, and inference actually work under the hood. No business content, no hype, no product reviews.

Examples of the content direction:
- *"the KV cache finally clicked for me: the first token is slow because the model reads your whole prompt at once, then caches every key/value so each next token only computes itself. that's why token 1 lags and the rest stream."*
- *"spent a day confused why fine-tuning didn't add the facts i wanted. it doesn't — fine-tuning shifts the output style, RAG is what injects facts. mixing these up is an expensive mistake i almost shipped."*
- *"quantization sounds like it should wreck a model. mostly it doesn't — int4 stores weights in 4 bits instead of 16, and the accuracy loss is tiny because the weights are noisy anyway. where does it start to hurt though?"*

---

## How It Works

```
GitHub Actions (11 AM / 3 PM / 9 PM IST)
        ↓
Pick a content SLOT   → news / educational / personal / advanced
Pick a STYLE          → alternates Style 1 / Style 2 every run
        ↓
Exa — neural web research
  • news slot: last-48h fresh releases
  • other slots: evergreen explanatory sources
        ↓
Gemini — writes the post in the chosen style + learning-engineer voice
  └─ fallback: Gemini key #2 → Euron API
        ↓
Infographic (skipped for the personal slot)
  Gemini → 3-stage JSON → Jinja2 → Playwright → 1800px PNG → hosted on imgbb
        ↓
Buffer — schedules post + attached infographic to Threads
```

Every post ends with one **topic tag**, a **follow CTA**, and the **portfolio link**:

```
#AgenticAI

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

3 posts per day, 7 days a week.

---

## Content Slots

Each run picks one slot (weighted), then a topic + tone from it. Defined in [`scripts/topics.json`](scripts/topics.json).

| Slot | What it posts | Research | Infographic |
|---|---|---|---|
| **news** | A new model / release, explained *technically* | Last 48h | ✅ |
| **educational** | How one mechanism works (RAG, KV cache, GPUs…) | Evergreen | ✅ |
| **personal** | Build-in-public lesson / story | Evergreen | ❌ (a story isn't a diagram) |
| **advanced** | Deep take for senior engineers | Evergreen | ✅ |

Bias the mix with `SLOT_WEIGHTS`, e.g. `SLOT_WEIGHTS="news:2,educational:3,personal:1,advanced:2"`. Default = equal weight.

---

## Post Styles (A/B test)

The slot decides *what* a post is about; the **style** decides *how* it's structured. Every post uses one of two fixed structures, and the pipeline **alternates them run-to-run** so you can compare which drives more views. The infographic is built in the same style, so image and text tell one story.

| | **Style 1 — problem → solution** | **Style 2 — scenario → solution** |
|---|---|---|
| **Arc** | State the problem → emphasize it → introduce the solution by name → prove it with concrete capabilities → leave a thought | Imaginary scenario → why it's critical → the real risk → the solution in **5 tight bullets** → why it clicked → a question for the comments |
| **Ends on** | A lingering thought | A comment-engagement question |

Alternation is deterministic (no state file): consecutive runs flip `1 → 2 → 1 → 2…`. Force one with `POST_STYLE` (`1` or `2`); leave blank to auto-alternate. Defined in [`scripts/generate_and_schedule.py`](scripts/generate_and_schedule.py) (`STYLE_GUIDANCE`, `pick_style`).

---

## Tech Stack

| Tool | Purpose |
|---|---|
| **GitHub Actions** | Scheduling (replaces VPS/cron) |
| **Exa** | Real-time neural web research |
| **Google Gemini** | Post + infographic-content generation (dual-key quota rotation) |
| **Euron API** | Last-resort fallback when all Gemini keys are exhausted |
| **Playwright + Jinja2** | Render the infographic HTML template → PNG |
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
# Preview — generates post + infographic, does NOT send to Buffer
python scripts/generate_and_schedule.py --preview

# Full run — research → post → infographic → schedule to Buffer
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
| `INCLUDE_INFOGRAPHIC` | `1` | Set `0` for text-only posts |
| `INFOGRAPHIC_HANDLE` | `@vipinailabs` | Handle shown on the infographic |
| `PORTFOLIO_URL` | `vipin-vishal.onrender.com` | Text in the infographic footer + the clickable link in the post |
| `INCLUDE_PORTFOLIO_LINK` | `1` | `0` drops the clickable link from the post body (regains reach) |
| `PORTFOLIO_CTA` | `See what I've been building →` | Lead-in before the portfolio link |
| `FOLLOW_CTA` | `follow @vipinailabs for daily dose of information` | The follow ask — use the **real** handle |
| `POST_STYLE` | _(blank)_ | Blank = alternate Style 1/2 each run; `1` or `2` forces one |
| `SLOT_WEIGHTS` | equal | Bias the slot rotation |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Primary model |
| `NEWS_WINDOW_HOURS` | `48` | Fresh-news window for the news slot |
| `DEFAULT_TOPIC_TAG` | `#AI` | Fallback topic tag |

> **Handle must be real.** Threads renders a tappable `@mention` only when the handle resolves — a wrong one silently degrades to plain text.
>
> **Reach trade-off:** Threads down-ranks posts containing a link, so the in-post portfolio link costs some views. Set `INCLUDE_PORTFOLIO_LINK=0` to drop it — the infographic URL and your Threads **bio** link (clickable, penalty-free) still promote you.

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
2. **(Optional) add variables** — `INFOGRAPHIC_HANDLE`, `PORTFOLIO_URL`, `POST_STYLE`, `SLOT_WEIGHTS`.
3. The workflow ([`.github/workflows/daily_post.yml`](.github/workflows/daily_post.yml)) runs at **11 AM, 3 PM, 9 PM IST**. Manual trigger: **Actions → Daily Threads Post → Run workflow**.

---

## Customizing Content

Edit [`scripts/topics.json`](scripts/topics.json):

| Key | What it controls |
|---|---|
| `niche` | The content category fed to Exa for research |
| `persona` | The voice/style context passed to Gemini |
| `content_slots` | The four slots, each with its own `label`, `topics`, and `tones` |

The two post styles live in `STYLE_GUIDANCE`, and the infographic template/fonts/portrait live in [`renderer/`](renderer/).

---

## Project Structure

```
├── scripts/
│   ├── generate_and_schedule.py   # main pipeline (slots, styles, post, schedule)
│   ├── infographic.py             # infographic content gen + imgbb upload
│   ├── topics.json                # niche, persona, content_slots
│   └── get_buffer_channel.py      # one-time helper to find Buffer channel ID
├── renderer/
│   ├── render.py                  # Playwright HTML → 1800px PNG
│   ├── templates/infographic.html.j2
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

No manual intervention needed on quota exhaustion.

---

## License

MIT
