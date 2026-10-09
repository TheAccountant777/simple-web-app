# World-class data journalism: global benchmarks, mapped to our pipeline

**Date:** 2026-10-09. **Purpose:** Turn the practices of the best data newsrooms into concrete rules for
the three stages of the Kenya Data Engine pipeline:

- **Engine:** research and verification (Plan 2)
- **Editorial:** the writing agent (Plan 4)
- **Design:** the chart and carousel agent

**Benchmarks studied:**
- The Economist, FT, NYT The Upshot, BBC, Reuters and Bloomberg Graphics
- ProPublica, Pew Research, Our World in Data, The Pudding
- Visual Capitalist and Chartr, as social-first examples
- The Sigma Awards, as the field's top prize
- Practitioner canon: Tufte, Cairo, Knaflic, Datawrapper, Smart Brevity
- Fact-checkers (Africa Check, PesaCheck), for the local proof of the same rules

> **Evidence caveat:** many of the sources below are secondary summaries or practitioner blogs. Platform
> "algorithm" claims and engagement benchmarks are vendor-reported and conflict with one another. Treat
> them as hypotheses to test with our own analytics, not as facts.

---

## 1. The one-sentence standard

**Every piece answers one reader question with verified numbers, shows the evidence on the chart, and
can be understood in under a minute.**

This standard combines what the benchmarks share:
- the Upshot's "if you can't explain it to someone else, the article failed";
- Reuters' "if a graphic needs a long explanation, it's too complicated";
- Visual Capitalist's *impact = attention × trust*: if either is zero, the impact is zero.

---

## 2. What the best do, in seven principles

### P1. Start with a question, not a dataset
The Pudding: "The heart of the process is finding a question you want to answer." The Sigma jurors
reward work that connects a complex system to real consequences for people. The Pudding also kills,
pauses or pivots most ideas. Not every topic deserves publication.
- **Engine (Plan 2):** the Planner's `core_question` is mandatory and phrased as a reader question.
- **Engine:** add a **story verdict** to every topic: `supported | reframed | reject`.

### P2. Bulletproof the data before anything is written (ProPublica)
ProPublica's bulletproofing checklist:
- record counts, and totals compared with published summaries;
- field consistency;
- plausible ranges and complete coverage;
- whether missing data is real or an artifact of import;
- methodology compared with similar published work;
- a running log of every step, ideally with the code;
- a second source for the same data.

Pew adds: describe methods "in sufficient detail to permit outsiders to evaluate the credibility," and
show uncertainty, including margins of error, directly on charts.
- **Engine:** these become the Data Scout's verification checks. Some already exist (sanity checks,
  spot-check, cross-source); add **totals reconciliation** against the publisher's own summary figure,
  a **coverage check** for missing months or regions, and a **method log** (`verification.md`) for each
  topic.
- **Engine:** **revisions awareness**. KNBS and CBK revise figures, so always record the release date
  and vintage of each number.

### P3. Separate fact, inference and speculation, and check the claim, not only the number
Africa Check's Kenya-versus-South-Africa check shows the pattern:
- one claimed figure was wrong by about 2×;
- KNBS and the World Bank disagreed slightly (4.9% against 4.7%);
- headline growth sat alongside widespread complaints of hard times, the gap between the aggregate
  and lived experience.
- **Engine:** each item in `claims.json` gets a `status` (**fact**: verified by quote or data;
  **inference**: computed in code from facts; **speculation**: never publishable) plus sources,
  quote, vintage and `conflicts[]`.
- **Editorial:** never smooth over a disagreement between sources; say which source and why.

### P4. One chart, one message, with the headline stating the takeaway
- **The Economist:** one message per chart; the key series in full colour and everything else grey.
- **FT (Burn-Murdoch):** charts with a strong narrative title and several annotations outperform
  minimalist ones; label the lines directly instead of using legends.
- **Knaflic:** context, then remove clutter, then direct attention with colour, size and position.

Use an **assertive headline**, for example "Petrol now costs KSh X more per litre than in January",
instead of a label such as "Fuel prices 2026".
- **Design:**
  - The headline is the finding, and a subtitle defines the measure and units.
  - Label data directly and add one to three annotations at the moments that matter.
  - Use colour for emphasis, not decoration.
  - Put the source and date on every image.

### P5. Choose the chart by the relationship, not by habit (FT Visual Vocabulary)
The FT's categories are **deviation, correlation, ranking, distribution, change over time,
part-to-whole, magnitude and spatial**. Pick the relationship the reader's question is about, then the
chart. Burn-Murdoch calls the vocabulary "a thinking device," not a rulebook. Use small multiples when
one chart would be too dense (The Economist).
- **Engine:** the Planner's `chart_concepts` must name the relationship type, for example
  `change_over_time`, together with the chart.
- **Design:** carry a decision tree that maps relationship to default chart for mobile:

  | Relationship | Default chart for mobile |
  |---|---|
  | Change over time | Line |
  | Ranking | Sorted horizontal bar |
  | Part-to-whole | Stacked bar, or a waterfall for price build-ups such as fuel taxes |
  | Deviation | Diverging bar |
  | Magnitude | Bar starting at zero |

### P6. Never mislead, even by accident (Cairo, *How Charts Lie*; The Economist's "Mistakes, we've drawn a few")
- **Truncated axes:** bars always start at zero. A line chart that doesn't start at zero must make that
  obvious; The Economist's rule of thumb is to leave about a third of the plot area free below the line.
- **Dual axes:** avoid them, because two scales can reverse the apparent relationship. Use two panels
  instead.
- **Cherry-picked windows:** justify the start date, and show the longer context when it changes the
  story.
- **Like with like:** compare the same town, the same fuel and the same units. Use real figures
  rather than nominal when purchasing power is the point.
- **Correlation is not causation:** wording must match the evidence (P3 statuses).
- **The Economist's three error types:** misleading, confusing, and hiding the point. The editorial
  gate checks for all three.

### P7. Transparency is a feature, not fine print
Best practice:
- **The Upshot:** publish the data behind its models.
- **Pew:** every release comes with a methods report.
- **Our World in Data:** "showing all the steps" is the default, and readers are left free to draw
  their own conclusions from clean, contextualised charts.

What that means for each stage:
- **Engine:** the dossier is the audit trail: `sources.json`, `stats.md` with formulas, and
  `verification.md`.
- **Editorial:** every post carries a compact source line. Where possible, link a "how we got this
  number" note (a public dossier summary later).

---

## 3. Social-first craft: LinkedIn and X

| Lever | What practitioners report | Our rule |
|---|---|---|
| **Format** | LinkedIn document or PDF carousels are consistently reported as the top format. Medians range from about 5.7% (Oktopost, B2B pages) to 21.8% (Buffer, 45M posts) engagement, so the numbers conflict. | Carousels for multi-step explanations. Single charts for one stark fact. Measure it ourselves. |
| **Size** | Portrait 4:5 at 1080×1350 is recommended for mobile feeds. | 1080×1350 for LinkedIn. 16:9 or 1:1 single image for X. |
| **First frame** | "Half the effort goes into the first slide." The Economist's social advice: open with the most surprising or relevant data point. | Slide 1 or the image headline carries the finding plus the reader's stake ("your fare", "your loan"). |
| **Density** | One idea per slide, roughly under 50–60 words. 5–15 slides, with 6–12 reported as best. | 6–9 slides: hook, context, evidence (2–4), what it means for you, what's next, source and method. |
| **Brevity structure** | Smart Brevity: a headline under 10 words, then "1 big thing", "Why it matters", "By the numbers", "What's next". | The default post skeleton for both platforms (see §4). |
| **Trust** | Visual Capitalist warns that a viral chart on shaky data erodes credibility long-term. | Evidence gate before craft gate. No publication without a dossier verdict of supported or reframed. |
| **Cadence and products** | Chartr built a product on short, frequent "visual snacks". Visual Capitalist built a repeatable system rather than chasing virality. | A recurring series cadence (§5) beats one-off virality. |

Dwell-time and "first-hour reply" algorithm claims are **unverified**. We will track saves, shares,
substantive comments and dwell (from LinkedIn analytics) rather than optimise to folklore.

---

## 4. Templates the editorial agent should use

**LinkedIn post (150–250 words)**
```
[Assertive headline: the finding, ≤10 words]
1 big thing: one sentence with the key number and what it means for a Kenyan household or professional.
Why it matters: 2–3 lines on the reader's stake.
By the numbers: 3 bullets, each traceable to stats.md / claims.json.
What we don't know: one line from gaps.md (builds trust).
What's next: the next date or release that will change this (calendar).
Source: publisher, release, date · method note
```

**X thread (1–4 posts):**
1. The chart, with an assertive headline and the number.
2. Why it matters.
3. The caveat or context.
4. The source and what to watch next.

**Carousel (6–9 slides):** hook, baseline, what changed (chart), why (decomposition), what it means
for you (a worked example, such as a 40-litre tank or a KSh 100k loan), what we don't know, what's
next, sources and method.

---

## 5. Recurring series (product thinking, à la Chartr and Visual Capitalist)

| Series | Trigger (calendar and radar) | Core chart |
|---|---|---|
| **Pump Price Check** | EPRA review, 14th monthly | Price per litre over time, plus a tax-stack waterfall |
| **Inflation, explained** | KNBS CPI, month end | Headline vs food vs fuel, with the real-wage angle |
| **Rate Watch** | CBK MPC decision | CBR vs lending rate vs T-bill yields |
| **Shilling Watch** | Weekly or monthly | KES/USD, plus a remittance overlay as a separate panel |
| **Myth vs Data** | Clarity-gap topics (CRB, MMF yields, tax claims) | The claim vs the verified figure |
| **Budget & Finance Bill** | Seasonal | What changes for a typical earner (worked examples) |

Series make the engine's calendar signals directly productive and train an audience to expect value.

---

## 6. Quality gates, run as two separate passes

**Evidence gate (blocking; owned by the engine plus an editorial check):**
- [ ] Every number appears in `stats.md` or `claims.json`, with source, vintage and formula.
- [ ] Like-for-like comparison: the same units, geography and series definition.
- [ ] The time window is justified, and the longer context has been checked.
- [ ] Conflicts between sources are disclosed. Inferences are worded as inferences.
- [ ] `gaps.md` items that affect the story are disclosed or the angle is narrowed.
- [ ] The story verdict is `supported` or `reframed`, never `reject`.

**Craft gate (blocking; editorial and design):**
- [ ] One reader question, one message, an assertive headline.
- [ ] The chart type matches the relationship (P5), with no misleading construction (P6).
- [ ] Mobile legibility at 1080×1350: direct labels, at most 3 annotations, readable text.
- [ ] Kenyan context and plain language: KSh, real-life worked examples, no jargon.
- [ ] Value first: no product pitch, at most a soft follow CTA.
- [ ] A source line is on the image.

---

## 7. Concrete changes this implies for Plan 2 (engine)

1. The Planner's `core_question` must be a reader question, and its `chart_concepts` carry the FT
   relationship type.
2. A `story_verdict` (`supported | reframed | reject`) with reasons, plus a contrarian or
   framing-challenge step: "what would make this story wrong?"
3. `research/claims.json` with a status for each claim (fact, inference or speculation), quote,
   source, vintage and conflicts.
4. `research/comparisons.csv`: like-for-like historical comparisons computed in code (same town,
   fuel and units).
5. Data Scout bulletproofing: totals reconciliation, coverage gaps, range checks, revisions and
   vintage, and a method log in `research/verification.md`.
6. Uncertainty fields such as revision notes and survey margins, carried into `stats.md`.
7. An acceptance test, the **fuel dossier**. The engine must:
   - find EPRA's prior schedules on its own;
   - compute the same-town per-litre change;
   - establish the VAT proposal's actual status from primary sources (Parliament bill text and
     National Treasury), resolving the "maintain 8%" versus "new cut" framing;
   - refuse unsupported claims.

---

## Sources
- Sarah Leo, The Economist, "Mistakes, we've drawn a few": https://medium.com/the-economist/mistakes-weve-drawn-a-few-8cdd8a42d368
- What makes The Economist's charts so good: https://medium.com/@timvanschaick/what-makes-the-economists-charts-so-good-0234e4271da3
- ProPublica, "Bulletproofing your data" (Jennifer LaFleur): https://github.com/propublica/guides/blob/master/data-bulletproofing.md
- GIJN, data-to-storytelling tips from John Burn-Murdoch (FT): https://gijn.org/stories/data-visualization-storytelling-tips-john-burn-murdoch/
- FT Visual Vocabulary (O'Reilly, *How Charts Work*, ch. 3): https://www.oreilly.com/library/view/how-charts-work/9781292342818/xhtml/Chapter03-00.xhtml
- Datawrapper, annotations: https://datawrapper.de/blog/better-more-responsive-annotations-in-datawrapper-data-visualizations
- Our World in Data, about: https://ourworldindata.org/about · Edouard Mathieu interview: https://hearthisidea.com/episodes/mathieu/
- The Pudding, process and pitch guidelines: https://www.storybench.org/pudding-structures-stories-visual-essays/ · https://pudding.cool/process/pivot-continue-down/ · https://pudding.cool/pitch/
- Sigma Awards 2026 winners (GIJN): https://gijn.org/stories/2026-data-journalism-sigma-award-winners/ · https://datajournalism.com/awards
- BBC Visual & Data Journalism R cookbook: https://bbc.github.io/rcookbook/
- Reuters Graphics onboarding, Graphics 101: https://reuters-graphics.github.io/docs_onboarding-guide/graphics_department/graphics_101/ · Reuters on visualising the AI economy: https://gijn.org/stories/reuters-data-visualization-graphics-ai-economy/
- NYT The Upshot (Nieman Lab Q&A with David Leonhardt): https://www.niemanlab.org/2014/04/qa-david-leonhardt-says-the-upshot-wont-replace-nate-silver-at-the-new-york-times/
- Pew Research Center mission and code of ethics: https://www.pewresearch.org/about/our-mission/ · U.S. surveys methods: https://www.pewresearch.org/u-s-surveys/
- Visual Capitalist playbook: https://elements.visualcapitalist.com/wp-content/uploads/2025/10/vc-playbook.pdf · Voronoi, "Start with the story, not the chart": https://about.voronoiapp.com/2025/05/26/start-with-the-story-not-the-chart/
- Chartr newsletters: https://www.chartr.co/newsletters
- EDJNet, data journalism on social media: https://medium.com/european-data-journalism-network/more-than-boring-numbers-data-journalism-on-social-media-ccb4b2ebd398
- Alberto Cairo, *How Charts Lie* (review): https://www.centerforcivic.org/post/book-review-how-charts-lie-getting-smarter-about-visual-information-by-alberto-cairo · Q&A: https://brown.columbia.edu/cairo-qa
- Misleading beyond visual tricks (CHI 2023): https://dl.acm.org/doi/10.1145/3544548.3580910
- Tufte, chartjunk and data-ink: https://data.europa.eu/apps/data-visualisation-guide/chart-junk-and-data-ink-origins
- Knaflic, *Storytelling with Data* (review): https://www.r-bloggers.com/2016/03/book-review-storytelling-with-data/
- Smart Brevity: https://www.smartbrevity.com/what-is-smart-brevity · checklist: https://www.axioshq.com/research/smart-brevity-communication-checklist
- LinkedIn carousel benchmarks (vendor-reported, conflicting): https://www.oktopost.com/blog/linkedin-carousel-pdf-best-practices/ · https://www.trymypost.com/blog/linkedin-pdf-carousel-design-guide-2026
- Africa Check, Kenya vs South Africa economic claims: https://africacheck.org/fact-checks/reports/kenya-vs-south-africa-fact-checking-claims-about-two-countries-contrasting
- Code for Africa / PesaCheck: https://en.wikipedia.org/wiki/Code_for_Africa
