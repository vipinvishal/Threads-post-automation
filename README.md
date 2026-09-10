# Threads Growth Automation

An evidence-backed content system for [@vipinailabs](https://www.threads.net/@vipinailabs). It discovers current technical AI demand, verifies one topic, writes a retention-focused Threads caption, generates one branded 9:16 handwritten infographic, schedules it through Buffer, and learns from real Threads insights.

No system can guarantee followers, views, clicks, or revenue. This one is designed to improve those outcomes without inventing facts, copying creators, or automating spam engagement.

## Daily loop

```text
8:30 AM IST: daily_learning.py
  Threads TOP/RECENT + Hacker News + Exa
        ↓
  10 scored technical-AI opportunities
        ↓
  real Threads insight snapshots
        ↓
  managed daily section in skills/threads-growth/SKILL.md

11 AM / 3 PM / 9 PM IST: generate_and_schedule.py
  highest-ranked unused candidate
        ↓
  fresh source retrieval + fail-closed evidence review
        ↓
  hook + useful retention beats + one objective/CTA
        ↓
  numeric fact gate + repetition/CTA gates
        ↓
  approved handwritten poster + blue bird mascot
        ↓
  imgbb hosting → Buffer → Threads
```

The scheduled workflows share one concurrency group so their Git-backed learning state cannot overwrite itself.

## What changed

- Current-demand discovery combines native Threads search when configured, Hacker News developer activity, and recent Exa coverage.
- Candidate ranking scores trend strength, relevance, practical value, originality, conversation potential, and evidence strength.
- A second research pass verifies the selected premise. Fresh claims need two supporting URLs; unsupported premises block publication.
- Captions target exactly one objective: reply, share, follow, or click. Follow and click asks cannot be combined.
- Exact numeric claims are detected even when the model omits them from its own claim list. Unsupported numbers block production runs.
- Every post uses the same tracked mascot/style reference and Gemini image model to create one phone-readable 9:16 poster. Carousel selection is disabled.
- Buffer rate-limit failures go to durable `scripts/pending_posts.json` and are not recorded as published.
- The optional Threads token reconciles real post IDs and stores insight snapshots for views, likes, replies, reposts, quotes, and shares.
- The daily skill update can replace only its managed section. Permanent safety/editorial rules do not drift with web content or a one-day result.

See [the research and design rationale](docs/threads_growth_research.md).

## Content and visual strategy

The niche is technical AI for Indian developers: LLM internals, agent systems, RAG, inference, GPU economics, developer tooling, and real regional costs.

Supported formats:

| Format | Primary job | Visual |
|---|---|---|
| `hot_take` | Timely replies | handwritten poster |
| `mechanism_explainer` | Trust and follows | handwritten poster |
| `practical_tips` | Saves and shares | handwritten poster |
| `india_cost` | Differentiation and commercial intent | handwritten poster |
| `quote_react` | Conversation | handwritten poster |

Live intelligence chooses the topic, format, and objective. Visual selection is locked to `handwritten_poster`: warm paper, bold marker lettering, pastel cards, highlighted keywords, the recurring blue bird mascot, and one strong takeaway. Topic details change daily; the identity does not.

## Quality gates

| Gate | Production behavior |
|---|---|
| Evidence | Selected premise must map to retrieved URLs; fresh claims require two sources. |
| Numeric claims | Unsupported exact claims fail closed when `STRICT_FACT_CHECK=1`. |
| Repetition | New hook and close are checked against recent history and recurring formulas. |
| CTA | At most one conversion CTA in the trailing eight-post window. |
| Objective | One of reply/share/follow/click; never both follow and click. |
| Visual | Exactly one `handwritten_poster`; generation failure blocks production posting. |
| Publishing | Pending Buffer posts are not mislabeled as successful history entries. |

## Schedule

| Job | IST | UTC cron |
|---|---:|---:|
| Intelligence + insights | 8:30 AM daily | `0 3 * * *` |
| Post 1 | 11:00 AM daily | `30 5 * * *` |
| Post 2 | 3:00 PM daily | `30 9 * * *` |
| Post 3 | 9:00 PM daily | `30 15 * * *` |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Required production secrets:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Candidate, evidence, caption, poster copy, and image generation |
| `EXA_API_KEY` | Current source retrieval and verification |
| `BUFFER_API_KEY` | Scheduling |
| `BUFFER_CHANNEL_ID` | Threads channel in Buffer |
| `IMGBB_API_KEY` | Public image hosting for Buffer |

Strongly recommended:

| Variable | Purpose |
|---|---|
| `THREADS_ACCESS_TOKEN` | Native TOP/RECENT discovery and first-party insight snapshots |

Optional: `GEMINI_API_KEY_2`, `GEMINI_IMAGE_MODEL`, `GEMINI_IMAGE_SIZE`, `EURON_API_KEY`, `INFOGRAPHIC_HANDLE`, `PORTFOLIO_URL`, `PORTFOLIO_CTA`, `FOLLOW_CTA`, `HUMANIZE_POST`, `INCLUDE_INFOGRAPHIC`, `REQUIRE_INFOGRAPHIC`, `STRICT_FACT_CHECK`, `NEWS_WINDOW_HOURS`, and `DEFAULT_TOPIC_TAG`.

Add the secrets under GitHub repository Settings → Secrets and variables → Actions. Both workflows already have `contents: write`, which they need to commit refreshed state.

## Run locally

```bash
# Refresh native metrics (when configured), research signals, candidate ranking, and skill memory
python scripts/daily_learning.py

# Generate a post and handwritten poster without publishing or modifying history
python scripts/generate_and_schedule.py --preview

# Full scheduled run
python scripts/generate_and_schedule.py

# Tests
PYTHONPATH=scripts python -m unittest discover -s tests -v
```

Preview mode still calls configured research/model services and may consume API quota. It does not call Buffer.

## Learning state

- `scripts/daily_intelligence.json`: today's source records and ranked candidates.
- `scripts/account_insights.json`: daily follower, click, and account-level engagement snapshots.
- `scripts/post_history.json`: publication metadata and real insight snapshots.
- `scripts/pending_posts.json`: recoverable Buffer rate-limit failures.
- `skills/threads-growth/SKILL.md`: stable editorial contract plus the managed daily intelligence block.

Only metrics from actual Threads insights become performance lessons, and at least six measured posts are required. Generated self-scores help rank opportunities but never become proof that a post performed.

## Project structure

```text
├── .github/workflows/
│   ├── daily_learning.yml
│   └── daily_post.yml
├── docs/threads_growth_research.md
├── assets/handwritten-poster-reference.png
├── scripts/
│   ├── daily_learning.py
│   ├── growth_intelligence.py
│   ├── threads_insights.py
│   ├── generate_and_schedule.py
│   └── infographic.py
├── skills/threads-growth/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   └── references/source-policy.md
└── tests/test_growth_system.py
```

## Operational notes

- Without `THREADS_ACCESS_TOKEN`, publishing still works through Buffer, but native trend search and performance learning remain disabled.
- In production, poster generation or hosting failure blocks the post. Set `REQUIRE_INFOGRAPHIC=0` only when a text-only fallback is explicitly desired.
- If Buffer remains rate-limited after retries, the content is saved as pending and the workflow reports the problem.
- Buffer is retained for stable scheduling. Direct Threads publishing would be the next upgrade if native `topic_tag` and per-image alt text are required.

## Author

Built and maintained by [Vipin Vishal](https://github.com/vipinvishal).
