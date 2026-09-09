# Evidence-backed Threads growth system

Research date: 9 September 2026 (IST)

## Executive conclusion

The sample infographic direction can help, but the format alone cannot reliably create growth. The current system discovers audience demand, verifies the underlying claim, and publishes an original Threads-native explanation. Results will be reviewed manually before any native account-access or automated performance-learning layer is introduced. No honest system can guarantee followers, views, clicks, or revenue.

The implemented strategy prioritizes qualified follower growth, then replies and amplification, then portfolio clicks. It does not automate mass engagement or manufacture authority.

## What the platform evidence says

Meta's creator guidance reports that replies account for almost half of views on Threads, posts that drive conversations are more likely to be recommended, higher posting frequency is linked to higher impressions, and posting 2-5 times per week is a baseline recommendation. It also says photo, video, and carousel posts that include text average more views, weekend posting can help, and top-performing creators tend to publish original content made for Threads. These are associations and platform observations, not guarantees of causal lift.[^meta-creator]

Meta also reports that posts with tagged topics generally receive more views, and eligible Threads posts may be recommended to people on Instagram and Facebook. Its newer Insights surfaces include interaction, follower-growth, and link-performance information.[^meta-topics]

## Design implications

### Topic discovery

Use two signal classes because they answer different questions:

- Hacker News top stories: what technical builders are actively reading and debating. Its official API exposes top/new/best stories and story score/comment fields.[^hn-api]
- Exa news search: wider recent coverage and source retrieval. Its official API supports date filters and returned text/highlights, while `numSentences` is deprecated in favor of highlights.[^exa-search]

Community activity discovers demand. It does not prove a technical claim. The selected premise is therefore researched again and must pass a separate evidence gate before generation.

### Content selection

Each candidate receives bounded 0-10 scores for trend strength, audience relevance, practical value, originality, conversation potential, and evidence strength. The system selects the highest-ranked unused topic while avoiding immediate format repetition.

The content types deliberately cover different growth jobs:

- `hot_take`: timely conversation, only with current evidence.
- `mechanism_explainer`: credibility and qualified follows.
- `practical_tips`: saves and shares through immediate utility.
- `india_cost`: differentiated commercial intent for Indian developers.
- `quote_react`: conversation around a traceable recent claim.

### Caption architecture

The post is written as one promise and one objective:

1. A specific hook, normally under 10 words.
2. Immediate payoff, not suspense filler.
3. Short retention beats that add new information.
4. A concrete mechanism, diagnostic, example, or trade-off.
5. One action: a narrow reply question, a specific share reason, a follow ask, or a portfolio click. Follow and click asks are never combined.

The system enforces plain text, a 500-character budget, recent-hook similarity checks, CTA frequency limits, and strict numeric-claim review.

### Infographic architecture

The supplied examples work best as inspiration for a clean, hand-drawn educational system. The implementation uses the account owner's portrait and brand rather than copying another creator's mascot or distinctive character.

A five-card 4:5 carousel is used when an idea needs multiple reading targets:

1. Cover: narrow hook and useful promise.
2. Problem: the audience's mistake or pain.
3. Mechanism: how the system actually works.
4. Example: diagnostic, before/after, or worked case.
5. Takeaway: one rule and one natural action.

Every card is capped at 48 words, uses at most three bullets, and is rendered at 1800 pixels wide. A dense poster is avoided because mobile viewers should understand each card without zooming.

### Learning loop

The daily job runs before the first post and performs two operations:

1. Refresh internet/community signals and rank ten opportunities.
2. Replace only the managed daily section of the repository skill.

Permanent safety and quality rules cannot be rewritten by a retrieved webpage. For now, post performance is checked manually and generated scores never become proof of effectiveness.

## Success metrics and experiments

During manual review, use rates so posts with different reach can be compared:

- Conversation rate: `replies / views`.
- Amplification rate: `(reposts + quotes + shares) / views`.
- Like rate: `likes / views`.
- Follow conversion: account follower change attributed cautiously across the posting window.
- Click conversion: portfolio clicks divided by views on click-objective posts.

Do not optimize all outcomes in one post. Run one clear objective and retain source, format, hook style, visual template, and CTA metadata. Compare medians over a meaningful sample; do not create a permanent rule from a single winner.

## Monetization path

Reach should lead to an owned, relevant destination rather than a generic portfolio homepage. The recommended sequence is:

1. Publish technical posts that solve a recurring engineering problem.
2. Use occasional click-objective posts to a matching deep-dive, tool, audit, template, or consultation page.
3. Measure Threads clicks and the destination's conversions separately.
4. Double down only when both audience growth and downstream conversion are healthy.

The code currently supports rare click-objective CTAs and portfolio links. A dedicated landing page and web analytics are business inputs, not claims the posting agent should invent.

## Operational risks

- Buffer may not expose Threads-native topic and alt-text controls. The current publisher keeps Buffer for continuity; direct Threads publishing can be reconsidered after the real posts are reviewed.
- News snippets can be wrong or adversarial. Retrieved text is treated as untrusted, and publication fails closed when evidence is insufficient.
- Three daily posts can become repetitive. Topic/format/history gates reduce this risk, but measured quality should determine whether frequency stays at three.
- Revenue cannot be inferred from views. Click and conversion tracking must exist before monetization choices are learned.

## Sources

[^meta-creator]: Meta, [Find Your Community With New Threads Educational Insights](https://about.fb.com/news/2024/10/find-your-community-with-new-threads-educational-insights/), 2024.
[^meta-topics]: Meta, [New Threads Features for a More Personalized Experience You Control](https://about.fb.com/news/2025/03/new-threads-features-more-personalized-experience-you-control/), 2025.
[^hn-api]: Hacker News, [Official API documentation](https://github.com/HackerNews/API).
[^exa-search]: Exa, [Search API reference](https://exa.ai/docs/reference/search).
