# Threads Post Automation

An AI agent that researches AI-systems topics, writes a technical Threads post **and** renders a matching hand-drawn-style infographic, then schedules both to Threads — **3 times a day, fully automated via GitHub Actions.**

**No VPS. No manual work.** Posts teach how AI actually works under the hood (architecture, infrastructure, inference) — not tool reviews or business fluff.

---

## How It Works

```
GitHub Actions (3× daily: 11 AM, 3 PM, 9 PM IST)
        ↓
Pick a content slot  →  news / educational / personal / advanced
        ↓
Exa — research
  • news slot: last-48h fresh releases
  • other slots: evergreen explanatory sources
        ↓
Gemini — writes a technical post in the systems-engineer voice
  └─ fallback: Gemini key #2 → Euron API (gemini-2.5-flash)
        ↓
Infographic (skipped for the personal slot)
  Gemini → 3-stage schema JSON → Jinja2 template → Playwright → 1800px PNG
  → hosted on imgbb (public URL)
        ↓
Buffer — schedules post + attached infographic to Threads
```

Each post ends with:
- a **specific, binary question** ("vLLM or TGI for serving?") to drive replies,
- one **topic tag** from the account's pinned Interests (`#AI`, `#AgenticAI`, `#CloudComputing`),
- a **standalone follow CTA** as the last line.

---

## Content Slots

Every run picks one slot (weighted), then a topic + tone from it. Defined in [`scripts/topics.json`](scripts/topics.json).

| Slot | What it posts | Research | Infographic |
|---|---|---|---|
| **news** | A new model/release, explained *technically* | Last 48h | ✅ |
| **educational** | How one mechanism works (RAG, KV cache, GPUs…) | Evergreen | ✅ |
| **personal** | Build-in-public lesson / story | Evergreen | ❌ (a story isn't a diagram) |
| **advanced** | Deep take for senior engineers | Evergreen | ✅ |

Bias the mix with the `SLOT_WEIGHTS` env/variable, e.g. `SLOT_WEIGHTS="news:2,educational:3,personal:1,advanced:2"`. Default = equal weight.

---

## Tech Stack

| Tool | Purpose |
|---|---|
| **GitHub Actions** | 3×/day scheduling (replaces VPS/cron) |
| **Exa** | Neural web research (per-slot recency) |
| **Google Gemini** | Post + infographic-content generation (dual-key quota rotation) |
| **Euron API** | Fallback when all Gemini keys are exhausted (`gemini-2.5-flash`) |
| **Playwright + Jinja2** | Render the infographic HTML template → PNG |
| **imgbb** | Hosts the PNG so Buffer can attach it (Buffer can't upload files) |
| **Buffer** | Schedules and publishes post + image to Threads |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/vipinvishal/Threads-post-automation.git
cd Threads-post-automation
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium # needed for infographic rendering
```

### 2. Configure

```bash
cp .env.example .env                  # then fill in your keys
```

### 3. Test locally

```bash
# Preview a post + render the infographic locally (no Buffer, no imgbb)
python scripts/generate_and_schedule.py --preview

# Full pipeline (research → post → infographic → host → schedule to Buffer)
python scripts/generate_and_schedule.py
```

---

## Configuration

Add these to your `.env` (local) or GitHub **Actions secrets** (automation):

| Variable | Where to get it | Required |
|---|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Yes |
| `GEMINI_API_KEY_2` | Same — second Google account | Optional (quota fallback) |
| `EURON_API_KEY` | [euron.one](https://euron.one) | Optional (last-resort fallback) |
| `EXA_API_KEY` | [exa.ai](https://exa.ai) | Yes |
| `BUFFER_API_KEY` | buffer.com → Settings → API | Yes |
| `BUFFER_CHANNEL_ID` | Run `python scripts/get_buffer_channel.py` | Yes |
| `IMGBB_API_KEY` | [api.imgbb.com](https://api.imgbb.com/) (free) | For infographics* |

\* If `IMGBB_API_KEY` is unset (or rendering fails), the run automatically falls back to a **text-only** post — a render hiccup never kills the daily post.

### Optional tuning (env vars / repo variables)

| Variable | Default | Effect |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Primary model |
| `EURON_MODEL` | `gemini-2.5-flash` | Euron fallback model |
| `INCLUDE_INFOGRAPHIC` | `1` | Set `0` for text-only posts |
| `INFOGRAPHIC_HANDLE` | `@vipin.vishal` | Handle shown on the infographic |
| `PORTFOLIO_URL` | `vipin-vishal.onrender.com` | Clickable link in the post body + text in the infographic footer (`""` disables the link) |
| `FOLLOW_CTA` | `Follow @vipin.vishal for 1 AI insight/day.` | Standalone last line |
| `SLOT_WEIGHTS` | equal | Bias the slot rotation |
| `NEWS_WINDOW_HOURS` | `48` | Fresh-news window for the news slot |
| `DEFAULT_TOPIC_TAG` | `#AI` | Fallback topic tag |

### Promoting your portfolio

`PORTFOLIO_URL` is promoted two ways on **every** post:

- **Clickable link in the post body** — appended as the last line so Threads auto-links it into a directly tappable URL.
- **Text in the infographic footer** — visible branding on image posts.

> **Trade-off:** Threads reduces the reach of posts that contain a link. A tappable link costs some views — that's the deliberate choice here. To avoid it, set `PORTFOLIO_URL=""` (which drops the clickable link but keeps the non-clickable URL on the infographic) and instead put your link in your **Threads bio**, which `FOLLOW_CTA` drives profile visits toward.

---

## GitHub Actions Setup

1. **Add secrets**: Settings → Secrets and variables → Actions → add
   `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, `EURON_API_KEY`, `EXA_API_KEY`,
   `BUFFER_API_KEY`, `BUFFER_CHANNEL_ID`, `IMGBB_API_KEY`.
2. **(Optional) add variables**: `INFOGRAPHIC_HANDLE`, `SLOT_WEIGHTS`.
3. The workflow ([`.github/workflows/daily_post.yml`](.github/workflows/daily_post.yml)) runs
   at **11 AM, 3 PM, 9 PM IST** and has a manual **Run workflow** button
   (Actions → Daily Threads Post → Run workflow).

---

## Customizing Content

Edit [`scripts/topics.json`](scripts/topics.json):

- **`niche`** / **`persona`** — the overall subject and voice.
- **`content_slots`** — the four slots, each with its own **`label`**, **`topics`**, and **`tones`**. Add/remove topics freely; the picker adapts to whatever slots exist.

The infographic template, fonts, and portrait live in [`renderer/`](renderer/).

---

## Project Structure

```
├── scripts/
│   ├── generate_and_schedule.py   # main pipeline (slots, post, schedule)
│   ├── infographic.py             # content JSON → render → imgbb upload
│   ├── topics.json                # niche, persona, content_slots
│   └── get_buffer_channel.py      # one-time helper to find Buffer channel ID
├── renderer/                      # infographic system (template, fonts, portrait)
│   ├── render.py                  # Jinja2 → Playwright → 1800px PNG
│   ├── templates/infographic.html.j2
│   ├── fonts/  data/  icons.py
├── .github/workflows/daily_post.yml
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Reliability

- **Fallback chain**: `Gemini key #1 → Gemini key #2 → Euron (gemini-2.5-flash)`.
- **Infographic fallback**: any render/upload failure or missing `IMGBB_API_KEY` → clean text-only post.
- **Buffer rate limits**: retried with backoff; if still limited, the post is saved to `pending_post.txt` to re-run later.

---

## License

MIT
