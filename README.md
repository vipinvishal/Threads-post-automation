# Threads Growth Automation

An evidence-backed content system for [@vipinailabs](https://www.threads.net/@vipinailabs). It discovers current technical AI demand, verifies one topic, writes a retention-focused Threads caption, creates a branded infographic or five-card carousel, and schedules it through Buffer.

No system can guarantee followers, views, clicks, or revenue. This one is designed to improve those outcomes without inventing facts, copying creators, or automating spam engagement.

## Daily loop

```text
8:30 AM IST: daily_learning.py
  Hacker News + Exa
        ↓
  10 scored technical-AI opportunities
        ↓
  managed daily section in skills/threads-growth/SKILL.md

11 AM / 3 PM / 9 PM IST: generate_and_schedule.py
  highest-ranked unused candidate
        ↓
  fresh source retrieval + fail-closed evidence review
        ↓
  hook + useful retention beats + one objective/CTA
        ↓
  numeric fact gate + repetition/CTA/template gates
        ↓
  4:5 carousel or focused infographic
        ↓
  imgbb hosting → Buffer → Threads
```

The scheduled workflows share one concurrency group so their Git-backed learning state cannot overwrite itself.

## What changed

- Current-demand discovery combines Hacker News developer activity and recent Exa coverage.
- Candidate ranking scores trend strength, relevance, practical value, originality, conversation potential, and evidence strength.
- A second research pass verifies the selected premise. Fresh claims need two supporting URLs; unsupported premises block publication.
- Captions target exactly one objective: reply, share, follow, or click. Follow and click asks cannot be combined.
- Exact numeric claims are detected even when the model omits them from its own claim list. Unsupported numbers block production runs.
- The new `educational_carousel` renders five 900×1125 cards (1800×2250 output) with strict mobile-readable copy limits.
- Buffer rate-limit failures go to durable `scripts/pending_posts.json` and are not recorded as published.
- The daily skill update can replace only its managed section. Permanent safety/editorial rules do not drift with web content or a one-day result.

See [the research and design rationale](docs/threads_growth_research.md).

## Content and visual strategy

The niche is technical AI for Indian developers: LLM internals, agent systems, RAG, inference, GPU economics, developer tooling, and real regional costs.

Supported formats:

| Format | Primary job | Visual choices |
|---|---|---|
| `hot_take` | Timely replies | stat, comparison, or text-only |
| `mechanism_explainer` | Trust and follows | carousel, flow, comparison, timeline |
| `practical_tips` | Saves and shares | five-card carousel |
| `india_cost` | Differentiation and commercial intent | stat, comparison, carousel |
| `quote_react` | Conversation | text-only or carousel |

The weekly rotation is now a fallback and variety constraint. Live intelligence normally chooses the topic, format, objective, and preferred visual.

The carousel follows the supplied infographic direction without copying another creator's mascot:

1. Cover hook and promise.
2. Audience problem.
3. Technical mechanism.
4. Concrete diagnostic/example.
5. Useful rule and one natural action.

## Quality gates

| Gate | Production behavior |
|---|---|
| Evidence | Selected premise must map to retrieved URLs; fresh claims require two sources. |
| Numeric claims | Unsupported exact claims fail closed when `STRICT_FACT_CHECK=1`. |
| Repetition | New hook and close are checked against recent history and recurring formulas. |
| CTA | At most one conversion CTA in the trailing eight-post window. |
| Objective | One of reply/share/follow/click; never both follow and click. |
| Visual | Template must suit the format and rotate when recently overused. |
| Publishing | Pending Buffer posts are not mislabeled as successful history entries. |

## Schedule

| Job | IST | UTC cron |
|---|---:|---:|
| Intelligence refresh | 8:30 AM daily | `0 3 * * *` |
| Post 1 | 11:00 AM daily | `30 5 * * *` |
| Post 2 | 3:00 PM daily | `30 9 * * *` |
| Post 3 | 9:00 PM daily | `30 15 * * *` |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Required production secrets:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Candidate, evidence, caption, and visual-content generation |
| `EXA_API_KEY` | Current source retrieval and verification |
| `BUFFER_API_KEY` | Scheduling |
| `BUFFER_CHANNEL_ID` | Threads channel in Buffer |
| `IMGBB_API_KEY` | Public image hosting for Buffer |

Optional: `GEMINI_API_KEY_2`, `EURON_API_KEY`, `INFOGRAPHIC_HANDLE`, `PORTFOLIO_URL`, `PORTFOLIO_CTA`, `FOLLOW_CTA`, `HUMANIZE_POST`, `INCLUDE_INFOGRAPHIC`, `STRICT_FACT_CHECK`, `NEWS_WINDOW_HOURS`, and `DEFAULT_TOPIC_TAG`.

Add the secrets under GitHub repository Settings → Secrets and variables → Actions. Both workflows already have `contents: write`, which they need to commit refreshed state.

## Run locally

```bash
# Refresh research signals, candidate ranking, and skill memory
python scripts/daily_learning.py

# Generate a post and carousel without publishing or modifying post history
python scripts/generate_and_schedule.py --preview

# Full scheduled run
python scripts/generate_and_schedule.py

# Tests
PYTHONPATH=scripts python -m unittest discover -s tests -v
```

Preview mode still calls configured research/model services and may consume API quota. It does not call Buffer.

## Learning state

- `scripts/daily_intelligence.json`: today's source records and ranked candidates.
- `scripts/post_history.json`: publication and content-choice history.
- `scripts/pending_posts.json`: recoverable Buffer rate-limit failures.
- `skills/threads-growth/SKILL.md`: stable editorial contract plus the managed daily intelligence block.

Performance is reviewed manually for now. Generated topic scores help rank opportunities but never become proof that a post performed.

## Project structure

```text
├── .github/workflows/
│   ├── daily_learning.yml
│   └── daily_post.yml
├── docs/threads_growth_research.md
├── scripts/
│   ├── daily_learning.py
│   ├── growth_intelligence.py
│   ├── generate_and_schedule.py
│   ├── infographic.py
│   └── infographic_templates.py
├── skills/threads-growth/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   └── references/source-policy.md
├── renderer/
│   ├── render.py
│   └── templates/educational_carousel.html.j2
└── tests/test_growth_system.py
```

## Operational notes

- Threads API access and automatic post-insight collection are intentionally disabled for now. Review the real posts before deciding whether to enable them later.
- If image rendering or hosting fails, the post falls back to text-only.
- If Buffer remains rate-limited after retries, the content is saved as pending and the workflow reports the problem.
- Buffer is retained for stable scheduling. Direct Threads publishing would be the next upgrade if native `topic_tag` and per-image alt text are required.

## Author

Built and maintained by [Vipin Vishal](https://github.com/vipinvishal).
