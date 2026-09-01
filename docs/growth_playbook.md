# @vipinailabs Growth Playbook

Companion doc to the pipeline changes in `scripts/generate_and_schedule.py`. Covers the
parts of the fix list that aren't code: bio, the daily engagement routine, and a 2-week
content calendar. See `docs/engagement_log_posts.csv`, `docs/engagement_log_replies.csv`,
and `docs/target_accounts.csv` for the tracking sheets.

---

## 1. What the code fixes now cover

Quick reference — see the code/README for detail:

- **Markdown leak (critical bug)**: fixed. `find_markdown_violations()` / `force_strip_markdown()`
  in `generate_and_schedule.py` guarantee no `**`, `*`, `_`, `[`, `]`, or `[label](url)` syntax
  ever reaches a posted body — regenerated first, force-stripped as a last resort either way.
- **Hook variety**: 5 rotating hook styles (contrarian / cold-open-stat / story-incident /
  myth-bust / question-first), pipeline-assigned per post — never repeats the immediately
  previous post's style, full coverage every 5 posts. See `HOOK_STYLES` / `pick_hook_style()`.
- **Brand identity**: standardized on **@vipinailabs** everywhere (handle, portfolio link,
  system prompt). The `orbitailabs` reference that was leaking into build_log posts — a
  leftover from earlier dead code — is gone from every prompt, example, and fixture.
- **Infographics**: body text sizes up ~40% across all 5 templates, per-field character
  budgets roughly halved (skimmable in under 2 seconds), a new `hero_stat` badge on
  `before_after` promotes the single most surprising number to a large headline element
  instead of burying it in a comparison box, and every day now offers a genuine 2-template
  choice (Tue/Thu previously had only one option each, which is likely why the feed looked
  like the same layout repeating).
- **Hashtags**: decided — **native Threads topic tag only** (`#AI` / `#AgenticAI` /
  `#CloudComputing`, one per post, auto-appended). Inline hashtags in the body are now an
  explicit VOICE RULE violation the model is told never to do.

## 2. Bio — 3 options

Threads bio has a ~150-character limit. Pick one, or mix lines:

**Option A — direct/utility-first:**
> AI/ML engineer explaining how LLMs, RAG & agents actually work — real mechanisms, real costs, no hype.
> vipin-vishal.onrender.com

**Option B — persona-first:**
> I test AI tools hands-on and share what's actually worth your time — no hype, just what works.
> vipin-vishal.onrender.com

**Option C — short and punchy:**
> How LLMs, RAG & agents really work, explained plainly. Daily breakdowns, no hype.
> vipin-vishal.onrender.com

All three replace "Daily insights from Delhi → orbitailabs." with (1) a clear who-this-helps
statement, (2) what they get, (3) one unambiguous link — matching the `vipin-vishal.onrender.com`
link the pipeline code already uses everywhere else, so the bio and the automated posts finally
point to the same place.

## 3. Engagement / reply routine

This is very likely the single biggest lever available — an account with zero outbound replies
has no reason for anyone to notice it exists. This has to be done by you (or with you in the
loop); it's not something the pipeline can safely automate.

### Finding 10-15 target accounts

I can't browse Threads live from here, so rather than naming specific handles I can't verify
are real, active, or the right size, here's the criteria + method:

**Criteria** — an account is a good target if:
- Posts genuinely technical AI/ML/agentic-AI content (not just news aggregation).
- Meaningfully larger than @vipinailabs (roughly 5k-500k followers) — big enough that their
  replies section has real traffic, small enough that a good reply doesn't vanish instantly.
- Active in the last few days, and — important — the account owner actually replies to
  comments on their own posts (check a few; an account that never engages back won't surface
  your reply to their audience either).

**How to find them:**
1. Open Threads' AI/tech topic tags (the same `#AI` / `#AgenticAI` pills this pipeline uses)
   and scroll a few days of posts — note who keeps showing up with real engagement.
2. Check who `@euron-official` (an account already in your reference material) follows,
   replies to, and gets replies from — that's a fast way into an adjacent, active AI audience.
3. Look at the *replies* under a few popular AI posts, not just the post authors — the
   people writing good replies there are often good accounts to also follow and engage with.
4. Once you have 3-4 solid accounts, Threads' "similar accounts" / suggested-follow surfacing
   after following them usually fills out the rest of the list quickly.

Keep the list in `docs/target_accounts.csv` so it's a living list, not a one-time exercise.

### The daily routine

Before or alongside your own post each day:

1. **Pick 2-3 target accounts** from your list — rotate through the full 10-15 over the week,
   don't hit the same 3 every day.
2. **Find one genuinely fresh post from each** (last 24h ideally).
3. **Write a substantive, specific reply** — the bar: it should be impossible to copy-paste
   onto a different post. Good replies usually do one of:
   - Add a concrete number or detail the post didn't mention.
   - Politely disagree with a specific claim and say why.
   - Ask a sharp follow-up question that shows you actually read it.
   - Share a one-line result from your own experience that's directly relevant.
   Avoid: "Great post!", "So true", "This 🔥" — generic praise gets ignored and teaches the
   algorithm nothing about you.
4. **Log it** — which account, which post, what you said, and check back in a day or two for
   whether it got likes/replies back, and whether their audience followed through to your
   profile (Threads doesn't show this directly, but a `views`/`profile clicks` bump right
   after a reply is a reasonable proxy if you check analytics that day).

15-20 minutes a day, done consistently, matters more than almost anything else in this list.

## 4. Two-week content calendar

The day of week locks the format (see `DAY_ROTATION`); the hook style is pipeline-assigned
per run using the real rotation logic (`pick_hook_style`) — this is the actual simulated
output, not a hand-picked example, so it's exactly what the pipeline will produce starting
from empty history:

| Day | Run | Format | Hook style | Image template pool | CTA? | Your reply-routine task |
|---|---|---|---|---|---|---|
| Mon (wk1) | 1/2/3 | Mechanism Explainer | Contrarian / Cold-Open Stat / Story-Incident | three_stage_flow, before_after | No | Reply to 2-3 accounts |
| Tue (wk1) | 1/2/3 | Hot Take | Myth-Bust / Question-First / Contrarian | single_stat_hero, before_after | No | Reply to 2-3 accounts |
| Wed (wk1) | 1/2/3 | Build Log | Cold-Open Stat / Story-Incident / Myth-Bust | annotated_screenshot, none | No | Reply to 2-3 accounts |
| Thu (wk1) | 1/2/3 | India Cost Check | Question-First / Contrarian / Cold-Open Stat | single_stat_hero, before_after | No | Reply to 2-3 accounts |
| Fri (wk1) | 1/2/3 | Mechanism Explainer | Story-Incident / Myth-Bust / Question-First | before_after, timeline | **Yes (1 run)** | Reply to 2-3 accounts |
| Sat (wk1) | 1/2/3 | Quote React | Contrarian / Cold-Open Stat / Story-Incident | none | No | Reply to 2-3 accounts |
| Sun (wk1) | — | *(off — reply-only day)* | — | — | — | **This is your main reply day** — go through the whole 10-15 list |
| Mon (wk2) | 1/2/3 | Mechanism Explainer | Myth-Bust / Question-First / Contrarian | three_stage_flow, before_after | No | Reply to 2-3 accounts |
| Tue (wk2) | 1/2/3 | Hot Take | Cold-Open Stat / Story-Incident / Myth-Bust | single_stat_hero, before_after | No | Reply to 2-3 accounts |
| Wed (wk2) | 1/2/3 | Build Log | Question-First / Contrarian / Cold-Open Stat | annotated_screenshot, none | No | Reply to 2-3 accounts |
| Thu (wk2) | 1/2/3 | India Cost Check | Story-Incident / Myth-Bust / Question-First | single_stat_hero, before_after | No | Reply to 2-3 accounts |
| Fri (wk2) | 1/2/3 | Mechanism Explainer | Contrarian / Cold-Open Stat / Story-Incident | three_stage_flow, before_after | **Yes (1 run)** | Reply to 2-3 accounts |
| Sat (wk2) | 1/2/3 | Quote React | Myth-Bust / Question-First / Contrarian | none | No | Reply to 2-3 accounts |
| Sun (wk2) | — | *(off — reply-only day)* | — | — | — | Main reply day |

Note on Friday's "Yes (1 run)": the day-lock makes all 3 Friday runs CTA-*eligible*, but the
rolling cap (`cta_cap_allows`) hard-blocks a 2nd CTA within any trailing 8 posts — so in
practice only the first Friday run of the day actually carries the follow CTA + link; the
other two automatically fall back to the plain topic-tag footer. Nothing to do here, it's
already enforced in code.

## 5. Engagement log

Three plain CSVs, git-tracked like the rest of the repo — open them in Numbers/Excel/Sheets:

- **`docs/engagement_log_posts.csv`** — one row per post the pipeline publishes. Fill in
  `views`/`likes`/`replies`/`reposts` a day or two after it goes out; `format`/`hook_style`/
  `image_template`/`cta_included` you can copy straight from that run's console output or
  `scripts/post_history.json`.
- **`docs/engagement_log_replies.csv`** — one row per reply you leave as part of the daily
  routine: which account, which of their posts, a short summary of what you said, and
  whether it got engagement back.
- **`docs/target_accounts.csv`** — the living list of 10-15 accounts from Section 3.

Columns worth watching over the first 2-3 weeks:
- **Replies received per hook style** — this tells you which of the 5 hook styles is actually
  landing with your audience, faster than views alone will.
- **Views the day after a reply-routine session vs. a day you skipped it** — the fastest way
  to confirm (or debunk) that the reply routine is the lever it looks like on paper.
