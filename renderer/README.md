# VipinAIHub Infographic System

Auto-generate branded, hand-drawn-style infographics for Threads, one per post,
driven by the pipeline in `../scripts/generate_and_schedule.py`. Change the
content JSON → get a correctly-laid-out PNG, every time, with no manual nudging.

5 templates share one renderer + one visual system (fonts, palette, spiral
binding, footer) via `templates/partials/`. Each template has its own content
schema, prompt, and coercion function in `../scripts/infographic_templates.py`.

## What's here

```
renderer/
├── templates/
│   ├── three_stage_flow.html.j2       # sequential "how it works" (3 boxes + arrows)
│   ├── single_stat_hero.html.j2       # one dominant real number
│   ├── before_after.html.j2           # a trade-off / comparison, 2 cards
│   ├── annotated_screenshot.html.j2   # stylized terminal/log + callouts
│   ├── timeline.html.j2               # 3-5 ordered milestones
│   └── partials/                      # shared spiral binding, footer, palette CSS
├── icons.py                    # reusable SVG icons, picked by name
├── render.py                   # Jinja2 Environment (5-template registry) → auto-fits text → PNG (Playwright)
├── fonts/
│   ├── *.ttf                   # handwritten fonts (Kalam, Caveat, Gochi Hand)
│   └── embedded_fonts.css      # fonts base64-embedded — NO network needed
├── data/
│   ├── portrait_b64.txt        # your portrait, embedded into every render
│   └── sample_*.json           # one example content JSON per template
└── output/                     # rendered PNGs land here
```

## Fonts (why they're embedded)

Google Fonts is often blocked on servers/CI. So the fonts are downloaded once,
base64-embedded into `fonts/embedded_fonts.css`, and injected at render time —
they render identically everywhere with zero network dependency.

- **Caveat** — the flowing marker headline script (titles, number circles)
- **Kalam** — hand-lettered body text. Bonus: it's an Indian Type Foundry font
  with Devanagari support, so it renders **Hindi / Hinglish** correctly too.
- **Gochi Hand** — available as an alt if you want to rotate styles.

To refresh or add fonts: drop a .ttf in `fonts/`, then rebuild the CSS:
```python
import base64
b64 = base64.b64encode(open('fonts/NAME.ttf','rb').read()).decode()
# append an @font-face rule to fonts/embedded_fonts.css
```

## Why HTML/CSS instead of hand-placed SVG

The original infographic was hand-tuned — every text-overflow and gap was fixed
by eye. A daily bot can't eyeball. CSS does the work instead: flexbox + text
wrapping + a font auto-shrink pass (`render.py: autofit()`) guarantee that
titles of any length fit their boxes. `test_https.json` has deliberately long
titles ("Client Hello & Certificate") and they wrap cleanly with zero edits.

## Run it

```bash
pip install playwright jinja2 --break-system-packages
python -m playwright install chromium

cd renderer
python render.py data/sample_content.json          output/infographic.png
python render.py data/sample_single_stat_hero.json  output/single_stat_hero.png
python render.py data/sample_before_after.json      output/before_after.png
python render.py data/sample_annotated_screenshot.json output/annotated_screenshot.png
python render.py data/sample_timeline.json          output/timeline.png
```

Output is 2x scale (~1800px wide) — crisp for social. Which template renders is
picked by the content JSON's own `_template_name` key (defaults to
`three_stage_flow` if absent) — `render.py` isn't given a template name on the
command line.

## The content schema (what the LLM must produce)

Each template has its own schema — see the per-template `REQUIRED_KEYS` /
`USER_TEMPLATE` in `../scripts/infographic_templates.py`, which is the single
source of truth (kept in sync with each template's Jinja variables). A few
fields are shared across every template and injected deterministically by
`../scripts/infographic.py`, never by the model: `handle` (always
`INFOGRAPHIC_HANDLE`) and `portfolio` (always `PORTFOLIO_URL`).

Available icon names (`icons.py`): upload, laptop, copies, database, lock,
cloud, gear, file, search, key, network. Icon resolution (name → raw SVG) is
generic in `render.py` — it recursively walks the content dict and swaps any
`icon` key's value, so every template gets this for free.

## Adding a 6th template

1. Add `templates/<name>.html.j2`, pulling in `partials/_shared.css.j2` /
   `_spiral.html.j2` / `_footer.html.j2` / `_bulb.html.j2` for the shared chrome.
2. Tag any variable-length text with `class="autofit" data-min-size="N"`
   (optionally `data-max-height="N"`) — `render.py`'s `autofit()` pass shrinks
   those generically, no new JS needed.
3. Register the file in `render.py`'s `TEMPLATE_FILES` dict.
4. Add a spec (system prompt, user template, required keys, coerce function) to
   `../scripts/infographic_templates.py`'s `TEMPLATE_SPECS`.
5. Reference the new template name from `DAY_ROTATION` in
   `../scripts/generate_and_schedule.py` wherever it should be an option.

**Quality guardrail:** always keep a human review pass at first on a new
template or format — one wrong technical claim posted automatically can cost
credibility. The pipeline's fact-check gate helps but isn't a substitute.
