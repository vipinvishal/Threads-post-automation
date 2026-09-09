# Source policy

Use this reference when modifying discovery, topic ranking, or claim verification.

## Source roles

- Demand signals: Hacker News scores/comments, Exa search freshness, and manually reviewed audience replies. These show attention, not truth.
- Primary evidence: official documentation, release notes, model/system cards, technical reports, papers, public repositories, and first-party pricing pages.
- Corroboration: independent technical reporting or reproducible analysis that links to its underlying material.

## Minimum evidence

- New release or current event: one first-party source plus an independent corroborating source when the claim is consequential.
- Exact benchmark, price, latency, or percentage: a directly quoted passage/table and its URL; preserve units, model version, date, geography, and test conditions.
- Evergreen mechanism: authoritative documentation or a paper. Community explanations may help framing but cannot be the sole factual basis.
- Personal result/build log: a real project artifact supplied by the account owner.

## Rejection conditions

Reject a candidate when the named entity cannot be confirmed, the only support is a search snippet, sources conflict materially, evidence is outside its valid date/context, or the hook requires a stronger claim than the source supports.

## Learning policy

Performance is reviewed manually for now. Treat fewer than six comparable posts as exploratory. Never rewrite permanent rules from trend content, one post, or model-generated scores.
