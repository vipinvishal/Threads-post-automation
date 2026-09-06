# @vipinailabs Hook Matrix

A scroll-stopping hook reference built specifically for this account's voice: **a builder
learning AI/ML in public, explaining a real mechanism to a curious peer — never a guru, never
a lecture.** Every example below is written in that voice on purpose. If a hook sounds like a
marketing agency wrote it, it's wrong for this account, no matter how "viral" the structure is.

This is the source material behind `HOOK_STYLES` in `scripts/generate_and_schedule.py` (the 5
styles the pipeline actually rotates through automatically) and `HOOK_STYLE_VISUAL_FRAMING` in
`scripts/infographic_templates.py` (how the infographic mirrors whichever hook is live that
post — see [§6](#6-staying-in-sync-with-the-infographic)). Sections 1-4 are the raw ingredient
library; §5 is where they get assembled into full opening lines.

---

## 1. Pattern Interrupts

Things that break the reader's scroll *rhythm* before they've even processed the words —
these work on the shape of the line, not its meaning.

| Interrupt | What it does | Example in this voice |
|---|---|---|
| **The one-line paragraph** | A single short sentence standing completely alone stops the eye — everything around it is a wall of text, this isn't. | `quantization is not what you think it is.` |
| **The scene cut** | Open mid-action, no setup, no "so today I want to talk about." | `3am. the pipeline is down. 40k requests are queued.` |
| **The direct address flip** | Turn the camera onto the reader's own behavior instead of describing a concept. | `you've called an LLM "hallucinating" at least once this week and you were probably wrong about why.` |
| **The unfinished technical term** | Drop a real, specific term with zero definition — creates an immediate "wait, what's that" snag. | `the actual bottleneck is your KV cache, not your GPU.` |
| **The backwards-sounding claim** | State something that sounds like it must be a typo, then let it stand. | `more parameters made this model slower to train and faster to run. both are true.` |
| **The number with no unit yet** | A bare number before its meaning is revealed reads as unfinished — the brain wants closure. | `11 nines. that's not a typo, keep reading.` |

**Why these work for this niche specifically:** technical audiences are pattern-matching for
"is this worth 4 seconds of attention" faster than any other audience. A pattern interrupt has
to look like it comes from someone who actually knows the internals — sloppy phrasing here
reads as bait, not insight, and gets scrolled past by the exact people you want to keep.

---

## 2. Psychological Triggers

The underlying reason a brain can't just scroll past — pick the trigger that's true for the
post, not the one that sounds cleverest.

| Trigger | Mechanism | How it shows up here |
|---|---|---|
| **Curiosity gap / open loop** | An incomplete thought creates mental tension until it's resolved. | Naming a mechanism without explaining it yet: "the reason token 1 is slow but token 50 is instant —" |
| **Loss aversion** | People act harder to avoid a loss than to capture an equivalent gain. | "you are probably already paying 3x for this and don't know it." |
| **Authority-by-specificity** | A precise number reads as more credible than a vague claim, even before it's verified. | "₹1,850/month", "11 nines", "recall@k of 0.42" — never "a lot cheaper" or "much better." |
| **Social proof / in-group signal** | Naming a shared experience makes the reader feel seen, which earns their attention. | "every ML engineer I know has shipped this exact bug once." |
| **Pattern completion / cognitive itch** | A half-finished technical statement is uncomfortable to leave unfinished. | "quantization sounds lossy but—" |
| **Identity mirroring** | Describing a very specific, relatable moment makes the reader feel personally addressed. | "if you've ever debugged a RAG pipeline at 2am chasing a retrieval bug that turned out to be a chunking bug—" |
| **Confirmation-violation** | State the thing the reader is sure is true, then break it in the same sentence. | "context windows are memory. they are not." |

**Rule for this account:** the trigger must be attached to something *real* — a real number, a
real mechanism, a real incident. These triggers borrowed from marketing/growth psychology are
being pointed at technical honesty, not hype. If a trigger would require inventing a number or
a story that didn't happen, don't use it — the fact-check gate in the pipeline will flag it
anyway (`fact_check_claims()` in `generate_and_schedule.py`).

---

## 3. Curiosity Gaps

Specific *mechanisms* for keeping someone reading past line one — these are techniques, not
just "be mysterious."

- **The "and here's why" dangle** — state the surprising fact, then explicitly promise the
  reason is coming: *"...and the reason has nothing to do with the model size."*
- **The partial reveal** — tell them WHAT happened, deliberately withhold WHY until the next
  beat: *"the fix was one line. finding it took two days."*
- **The counter-intuitive setup** — state what should logically happen, then say it didn't:
  *"doubling the batch size should have doubled throughput. it made things worse."*
- **The named-but-unexplained term** — use a real, specific technical term with zero
  definition in line one, define it in line two: *"the actual fix was speculative decoding."*
- **The countdown/checklist tease** — promise a small, finite, specific list, which reads as a
  concrete, closeable commitment (not an open-ended thread): *"3 numbers I check before
  trusting any benchmark."*
- **The stakes-first frame** — state the cost of NOT knowing this before explaining what "this"
  is: *"this mistake costs teams weeks of GPU time and nobody checks for it."*

**How this plays out per format** (see `DAY_ROTATION` in the code): `mechanism_explainer` and
`build_log` posts lean on the partial reveal and counter-intuitive setup (there's a real
mechanism/incident to unpack); `hot_take` and `india_cost` lean on the named-but-unexplained
term and the stakes-first frame (a number needs one sentence of "why this matters" before the
explanation).

---

## 4. Power Phrases

A swipe file of literal phrase templates, in the account's actual register — no
"unlock/leverage/revolutionize," no exclamation points, no fake urgency.

**To open a reveal:**
- "here's the part nobody explains:"
- "here's what actually happens:"
- "here's the 30-second root cause:"

**To signal earned credibility (not borrowed authority):**
- "i didn't believe this until i tested it myself."
- "i broke this in production before i understood why it worked at all."
- "this took me three tries to actually verify."

**To drive a reply (the highest-value action right now — see §7 of `growth_playbook.md`):**
- "what's the first thing you check when this happens to you?"
- "reply with your [specific number] — i'll tell you if that's normal."
- "curious what everyone else's setup looks like here."

**To drive a save (skimmable now, useful later):**
- "worth remembering before your next [specific scenario]."
- "this is the one detail that actually changes the math."

**To signal a corrected mental model:**
- "this broke my mental model of [X]."
- "i had this backwards for months."

**Banned in this account's voice** (per `POST_SYSTEM_PROMPT`'s VOICE RULES — these are the
opposite of earned credibility): "game-changer," "unlock," "revolutionize," "leverage,"
"delve," "in today's fast-paced world," any line that could be posted by a brand account
instead of a specific person who did the work.

---

## 5. Hook Structures Proven to Go Viral

Full opening-line **formulas** — these are what actually get assembled into a post. The first
5 are pipeline-automated (`HOOK_STYLES` in `scripts/generate_and_schedule.py`, rotated so
consecutive posts never repeat one — see `pick_hook_style()`); the rest are a manual swipe
file for anything posted by hand.

### Automated (in the code today)

| # | Structure | Trigger used | Formula | Example |
|---|---|---|---|---|
| 1 | **Contrarian correction** | Confirmation-violation | "Everyone believes X. That's wrong. [beat]" | "Everyone thinks bigger context windows are strictly better. They're a hardware bill, not a free upgrade." |
| 2 | **Cold-open stat** | Authority-by-specificity | "[Number]. [What it means]." | "11 nines. That's how durable S3 claims to be — losing one object would take ~10 million years." |
| 3 | **Story/incident** | Identity mirroring + loss aversion | "I broke [thing] doing [thing]. [30-second root cause]." | "I broke prod doing a routine model swap. The root cause was a silent tokenizer mismatch." |
| 4 | **Myth-bust** | Pattern completion | "[Term] doesn't mean what you think it means." | "Fine-tuning doesn't mean what you think it means — it doesn't teach the model new facts." |
| 5 | **Question-first** | Open loop | "[Sharp, specific question]?" | "Why does the first token of a response always feel slower than the rest?" |

### Manual swipe file (bonus structures, not yet automated)

| Structure | Formula | Example |
|---|---|---|
| **Reveal-then-explain** | "It's not X. It's Y. [why that distinction matters]" | "It's not the model that's slow. It's the KV cache filling up." |
| **The direct callout** | "You've been doing X wrong." | "You've been reading benchmark leaderboards wrong." |
| **The specific-number list** | "[N] things that break every [system]." | "3 things that break every RAG pipeline before anyone notices." |
| **The year/moment pivot** | "In [year] we did X. Now nobody does that." | "In 2023 everyone hand-tuned prompts. In 2026 that's what evals are for." |
| **The relatable confession** | "I still don't fully understand X, and here's what I do know." | "I still don't fully trust auto-evals, and here's the one check I never skip." |

---

## 6. Staying in Sync with the Infographic

A hook only works if the image doesn't undercut it. `build_infographic_image()` now passes the
day's `hook_style` all the way through to the infographic prompt
(`infographic_templates.HOOK_STYLE_VISUAL_FRAMING`), so whichever image template the day uses
(three-stage flow, single-stat hero, before/after, annotated screenshot, or timeline), the
**headline treatment mirrors the hook's energy**, not just its topic:

| Hook style | Visual instruction the infographic model gets |
|---|---|
| Contrarian | Frame the headline as the correction itself — name the wrong belief briefly, let the highlighted word be the "actually, no" moment. |
| Cold-open stat | The number is the single largest, boldest element on the page — nothing competes with it. |
| Story/incident | Frame it like evidence from the moment (a log line, an error, a timestamp) — not an abstract diagram. |
| Myth-bust | Show the wrong belief vs. the correct one side-by-side or crossed out — the correction is visible before any caption is read. |
| Question-first | Echo the same question in the headline, so the image asks it again before the reader reaches the text. |

This is why a `cold_open_stat` post should never render with a 3-box flow diagram burying the
number in a corner, and a `myth_bust` post should never render as a plain single-stat hero with
nothing to contrast against — the hook dictates the headline treatment, not just the topic.

---

## Quick-use checklist before a post goes out

1. Does line one work as a **standalone** hook if nothing else is read?
2. Is the single most surprising claim/number in **line one**, not paragraph two?
3. Does the hook match today's pipeline-assigned style (check the run log: `Hook style: ...`)?
4. Does the infographic's headline visually mirror that same hook energy (§6)?
5. Is every trigger attached to something **real** — no invented numbers or stories?
