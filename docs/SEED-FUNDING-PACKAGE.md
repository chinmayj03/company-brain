# Seed Funding Package — Demo + Deck + Outreach

**Audience:** CEO + GTM team
**Goal:** Three artifacts you can execute on this week to start raising the seed round.
**Time to execute end-to-end:** 7 working days for one founder + a part-time designer.

---

## A. The 90-second demo script

The demo is the entire pitch. If this lands, the deck and the meeting close themselves.
If this doesn't land, no deck saves you.

**Setup (do once, before any investor meeting):**

1. Pre-extract 3 famous repos into a public sandbox brain. Recommended:
   - **Stripe `stripe-node`** — every CTO has integrated Stripe; payment-intents flow is the canonical "this matters" example
   - **Vercel `next.js`** — every web dev knows it; routing/middleware is rich for SQL-and-edge questions
   - **Anthropic `mcp` repo** — meta-flex; "we ran our brain on the company building the protocol we use"
3. Have all 3 brains pre-loaded in the demo UI; investor never waits for extraction.
4. A second monitor open with **Cursor pointing at the same Stripe repo**, MCP-connected to your brain.
5. Open ChatGPT-4o in a third tab, ready to type the same question for side-by-side.

**The script (read these lines verbatim, the timing matters):**

```
[0:00–0:10]  SETUP
"Every AI coding tool today has the same blind spot: it doesn't know YOUR
codebase deeply. We're the missing memory layer.

Let me show you on Stripe's open-source SDK."

[Open the brain UI, pre-loaded with stripe-node]

[0:10–0:35]  THE WOW QUESTION
"Question: 'If I rename the customer_id column, what breaks?'"
[Type. Hit enter.]

[Brain responds in <3 seconds: structured answer with —
  - 47 affected files across 6 directories
  - 12 SQL chains quoted with file:line citations
  - 3 owning teams (inferred from git blame)
  - Risk: HIGH
  - Suggested rollout sequence
  - Blast-radius graph rendered visually]

"In 2 seconds. With citations. Anyone can click any line."

[0:35–0:55]  THE COMPARISON
"Same question to GPT-4o."
[Switch to ChatGPT tab. Paste: 'If I rename the customer_id column in
Stripe's stripe-node SDK, what would break?']

[GPT responds: 'I don't have access to Stripe's specific codebase.
Generally, you'd want to check for...']

"This isn't because GPT is bad. It's because raw LLMs don't have the
codebase. We do — extracted, indexed, queryable, with citations."

[0:55–1:25]  THE STRATEGIC FLAG (the moment that changes the room)
"Now watch what happens in Cursor."

[Switch to Cursor. Open a Stripe file. Right-click on a Customer object.
'Ask Brain.']

[Brain context flows in. Cursor's autocomplete now references the brain.]

"Cursor just got 10× smarter on Stripe's specific codebase — without
Cursor doing anything. We're the brain Cursor plugs into. We can plug
into Cognition's Devin, GitHub Copilot Enterprise, Sourcegraph Cody,
your internal F500 copilot — anyone via MCP."

[0:25–0:30]  THE LANDING
"AI coding tools are exploding. They all have the same blind spot.
We're the picks-and-shovels."

[Stop talking. Wait for the question.]
```

**The first question they ask predicts the meeting outcome:**

- *"How does this work technically?"* — CTO is bought-in. Spend 5 minutes on architecture, then close.
- *"Who's paying you?"* — Partner is interested. Move to slide 4 (traction).
- *"What about [Cursor / Sourcegraph / Glean]?"* — They're triangulating the category. Lean in: explain why we're complementary, not competitive.
- *"What's the business model?"* — They're closing themselves. Move to slide 5 (ask).
- *Silence* — Bad. Pivot to the time-travel slider or another canned question.

**Three backup canned questions** if they want to drive:

1. *"Show me everywhere `process.env` is read in this repo, grouped by what it's used for."* — proves cross-cutting analysis, not just call graphs.
2. *"This commit changed file X. Who should I notify?"* — proves the people-graph + ownership inference.
3. *"What did this endpoint look like 3 months ago?"* — proves time-travel (this is unique vs raw context windows).

---

## B. The 5-slide deck

5 slides. Not 30. Each slide has ONE message. If the slide needs explanation, cut content until it doesn't.

### Slide 1 — Problem

**Visual:** Screenshot of Cursor giving a generic answer to a repo-specific question. Big red circle around the bad part.

**Headline:**
> *AI coding tools don't know YOUR codebase.*

**Sub-line:**
> 90% of dev questions are repo-specific. AI tools answer them generically.
> Engineers waste hours rewriting AI suggestions to match their conventions.

**What you say (15 seconds):**
"Every CTO at a 100+ engineer org has heard this complaint: 'Cursor doesn't
understand our codebase'. There's no fix today — Cursor has no way to
know your private conventions, your ownership, your historical decisions."

### Slide 2 — Category

**Visual:** A 2×2 with axes: "Generic ↔ Org-specific" × "Read-only ↔ Active".
Plot Cursor (Generic + Active), Sourcegraph (Org + Read-only),
Glean (Org + Read-only, non-code), and **us in the empty quadrant**:
Org-specific + Active substrate for other AI.

**Headline:**
> *We're the institutional memory layer for software.*

**Sub-line:**
> Not a coding agent. Not a search tool. The brain other tools query.

**What you say (20 seconds):**
"Sourcegraph is search. Cursor is autocomplete. Glean is org search but not
code-aware. There's a fourth quadrant — org-specific knowledge that AI
agents call. We're building it. Sourcegraph hit $100M ARR; Glean is at $4B
valuation. There's no equivalent for what we do."

### Slide 3 — Demo

**Visual:** Embedded 90-second video (the demo from section A above).

**Headline:**
> *(Just the video. No headline.)*

**What you say:** Nothing. Press play. Stay quiet for 90 seconds. Let the
product speak.

If asked questions during the video, hold up a finger and say "let it finish
— it's only 90 seconds". Investors who interrupt the demo are signal.

### Slide 4 — Why now + traction

**Visual:** Three boxes side-by-side:
1. **Market timing** — chart of AI coding tools usage exploding 2024→2026
2. **Regulatory tailwind** — EU AI Act effective August 2026; SOC2 + AI requirements expanding
3. **Our traction** — 3 design partners (or whatever number is true), 1 AI-vendor LOI (target by month 3)

**Headline:**
> *Three tailwinds, all in our window.*

**Sub-line:**
> AI tools are scaling faster than their context. Regulators are demanding
> documented system understanding. We are the answer to both.

**What you say (30 seconds):**
"Three things made this possible right now. One: AI coding tools went from
0 to $1B ARR in 18 months — they need a context substrate, urgently. Two:
EU AI Act August 2026 mandates documented system understanding for any
AI in production — auditors need this, and there's no tool today. Three:
context windows hitting 10M tokens make 'paste your repo' technically
possible but legally and economically impossible at enterprise scale —
we're the cost-and-governance layer."

### Slide 5 — Ask

**Visual:** Simple table:
| Round | Amount | Use of funds | Milestone |
|---|---|---|---|
| Seed | $X | 18 months runway | $1M ARR + 1 AI-vendor partnership |

**Headline:**
> *$X for 18 months. Get us to Series A.*

**Sub-line:**
> 5-person team: 2 eng, 1 founder, 1 founding designer, 1 GTM hire.
> Milestones: 10 paid pilots, 1 AI-vendor partnership, $500K ARR, 130% NRR.

**What you say (20 seconds):**
"We're raising $X to do three things in the next 18 months. One: ship the
production-grade MCP server and sign one major AI-tool partnership — Cursor,
Cognition, or GitHub Copilot. Two: 10 paid pilots with VPs of Eng at
50-500-engineer orgs, average $50K ACV, validating the enterprise
motion. Three: ship the compliance product so we have a regulatory wedge
for Series A. We have a clear path. Want to walk through the milestones?"

**Total deck: 5 slides. Total presentation time: 7 minutes. Demo: 1.5 minutes. Q&A: 10–15 minutes. Meeting fits in 30 minutes.**

---

## C. The cold-email template

Three audiences. Three subject lines. Same body skeleton with three variants.

### C1. To VPs of Engineering at 50-500-eng orgs (Persona 1)

**Subject lines (A/B test):**
- `Bus factor for [Company]'s codebase` ← strongest hook, executive language
- `Quick question about onboarding at [Company]`
- `Helped [SimilarCo] cut new-hire ramp from 3 months to 3 weeks`

**Body:**

> Hi [First name],
>
> [Company] is around [N] engineers — at that size most VPs I talk to
> say onboarding takes 3 months and one senior engineer's departure is
> a board-level conversation.
>
> I'm building Company Brain — it extracts your codebase into a
> queryable knowledge graph. New hires query it on day one to understand
> the system. You query it before approving a refactor to see the blast
> radius. Auditors query it for SOC2 documentation.
>
> 90 seconds, on Stripe's open-source SDK:
> [link to Loom of the demo]
>
> I'd love 20 minutes to show you the same thing on a private fixture
> and ask whether this would have prevented the last "Sarah is leaving"
> moment at [Company].
>
> No setup needed. I'll bring the screen-share.
>
> Worth a chat?
>
> [Name]
> [Calendar link]

**Why this works:**
- Specific: "around N engineers" forces them to read past the first line
- Names a problem they actually have (executive-language: "board-level conversation")
- Shows the demo upfront (no "would you like to see" — assumes value)
- Low ask: 20 minutes, no setup, you do the work
- The "Sarah leaving" frame triggers a real memory; they'll think of a name

**Personalisation per email (don't skip):**
- "around N engineers" — get from LinkedIn / their team page
- One specific recent thing: "Saw [Company] launched [product] last month — congrats"
- If you can: "[Mutual connection] thought we should talk"

### C2. To Platform / Founding Engineers at AI-tool vendors (Persona 2)

**Targets:** Cursor, Cognition (Devin), Sourcegraph (Cody), GitHub Copilot Enterprise PMs, internal AI-platform leads at Stripe / Airbnb / Spotify / etc.

**Subject lines:**
- `Memory layer for [their product]` ← straight to the point
- `[Their product] + repo-specific context`
- `Quick MCP integration idea for [their product]`

**Body:**

> Hi [First name],
>
> [Their product]'s biggest customer complaint, from what I've heard from
> users, is "doesn't know our codebase deeply enough." That's not a model
> problem — it's a context problem. Pasting the whole repo doesn't scale
> on enterprise monorepos and is a non-starter for regulated customers.
>
> I'm building Company Brain: a per-org persistent context layer that
> [Their product] could query via MCP. 2-second responses, citation-
> grounded, governance-ready.
>
> Demo on Stripe's repo (90s): [Loom link]
>
> Would 30 minutes next week work to explore whether we should be the
> default brain in [Their product]'s MCP marketplace? Happy to wire up a
> POC against your dev environment so you can feel it.
>
> [Name]
> [Calendar link]

**Why this works:**
- Names their specific user complaint (do your research; Cursor's GitHub issues are public)
- Frames YOU as the solution to their problem, not asking them to be your customer
- "Default brain in your MCP marketplace" — specific, ambitious, ego-flattering
- Offers to do the integration POC work for free

### C3. To CISOs / Compliance / Security leads (Persona 3)

**Subject lines:**
- `EU AI Act prep — automated system documentation`
- `SOC2 evidence pack from your codebase, automatically`
- `Quick question about [Company]'s AI Act readiness`

**Body:**

> Hi [First name],
>
> EU AI Act takes effect August 2026. Article 13 requires documented
> system architecture for any AI in production — including the AI tools
> [Company]'s engineers use to write code. Most companies will scramble
> to produce this manually.
>
> I'm building Company Brain — extracts your codebase + AI usage into a
> queryable knowledge graph. Auditors get cited, code-grounded
> documentation generated automatically, refreshed weekly.
>
> Same engine produces SOC2 Type II evidence packs and PII data lineage.
>
> 20 minutes to walk you through what this looks like for [Company]?
>
> [Name]
> [Calendar link]

**Why this works:**
- Regulatory deadline is a forcing function (real budget moves around it)
- Names the specific Article (proves you've done homework)
- Compliance officers love things that auto-refresh (manual doc is their pain)
- Short ask, specific timeline

### Follow-up sequence (3-touch)

If no reply, send these on day 4 and day 10:

**Day 4 — soft bump:**
> Hi [First name], bumping this in case it got buried. The Loom is 90
> seconds: [link]. Worth a chat? [calendar]

**Day 10 — value-add break-up:**
> Hi [First name], I'll stop bumping after this. Wanted to share a
> short writeup I did on what's coming for AI codebase context in
> 2026 [link to a 1-page Notion or blog post]. Useful regardless of
> whether we work together. Best of luck.

The day-10 email gets responses from people who would have ghosted. The
"break-up" framing reduces social pressure; the value-add gives them
something to react to.

### Outreach math

If you send **30 personalised emails per persona × 3 personas = 90 emails:**

- Realistic open rate: 50% = 45 opens
- Reply rate (cold, well-written): 10–15% = **9–14 replies**
- Conversion to call: 60% of replies = **5–9 calls**
- Conversion to design partner: 20% of calls = **1–2 design partners**

That's enough for the seed round if you also have 1 AI-vendor LOI.

**Cadence:** 30 emails / week × 3 weeks = 90 emails. One founder can do this
solo in ~10 hours/week if templates are pre-built.

---

## Execution checklist (7 working days)

**Day 1 (Monday)**
- Pre-extract Stripe + Vercel + Anthropic-MCP repos into a public-sandbox brain.
- Confirm extraction results are good (sanity-check the canned questions).

**Day 2 (Tuesday)**
- Build the blast-radius visualisation (`react-flow`, ~6 hours).
- Wire up the side-by-side ChatGPT comparison panel.

**Day 3 (Wednesday)**
- Wire the existing VS Code extension to talk to the MCP server (verify
  Cursor demo works).
- Record the 90-second demo Loom (do 5 takes; pick the best).

**Day 4 (Thursday)**
- Build the 5-slide deck (Pitch.com or Google Slides; design polish later).
- Create the landing page (v0 / Lovable, ~3 hours).

**Day 5 (Friday)**
- Build the 90-target outreach list:
  - 30 VPs of Eng at 50-500 eng orgs (use LinkedIn Sales Nav or Clay)
  - 30 platform/founding engineers at AI-tool vendors
  - 30 compliance officers at regulated companies
- Personalise the first 30 emails (Persona 1 — VPs of Eng).

**Day 6 (Saturday — optional or week 2)**
- Send first 30 emails (Persona 1).
- Schedule any inbound calls for week 2.

**Day 7 (Sunday — buffer)**
- Slack with first replies (if any). Schedule first calls.

**Week 2:** Take the calls, send Persona 2 emails (AI-vendor).
**Week 3:** Send Persona 3 emails (compliance), refine deck based on call learnings.

By end of week 3 you should have **5–9 booked calls** and a deck refined by
real-investor reactions.

---

## What to NOT do this week

- Don't perfect the harness migration (ADR-0051 P1-P4). Ship after seed closes.
- Don't perfect the VS Code extension UI. Existing 270 LOC is enough.
- Don't write more ADRs. The strategy is set.
- Don't build new features. The product is enough; the demo is the bottleneck.
- Don't redesign the brain UI from scratch. Polish 2 screens (the query view + the blast-radius view) and call it done.
- Don't hire before the seed round closes. One designer contractor is fine; full FTE adds before money is anchor on the boat.

---

## Metrics to track

| Metric | Target by week 3 | Target by week 8 |
|---|---|---|
| Cold emails sent | 90 | 200 |
| Reply rate | 10% | 12%+ |
| Calls booked | 5–9 | 20+ |
| Design partner LOIs | 1 | 3 |
| Investor meetings booked | 3 | 10+ |
| AI-vendor partnership conversations | 1 | 3 |
| Deck iterations | 3 | 6+ (each meeting refines it) |

If you're below targets after week 3, the bottleneck is one of:
- **Demo doesn't land** → re-cut the Loom; the question matters more than the visualisation
- **Subject lines bad** → A/B test more aggressively; CTOs reply to specific things
- **Wrong targets** → 50-500 eng VPs of Eng is the strike zone; adjust if needed

---

## TL;DR for the CEO

1. **Seed-round MVP is a 90-second demo, not a product feature** — pre-extract 3 famous repos, record one Loom, that IS the MVP.
2. **5-slide deck. 7 minutes presented. 30-minute meeting.** Anything longer is wasted time.
3. **90 cold emails over 3 weeks** to three personas; expect 5–9 calls and 1–2 design partner LOIs out the back end.
4. **Stop building.** The product is enough. The bottleneck is putting it in front of humans.
5. **The strategic flag in every meeting is the Cursor MCP integration** — that's what tells investors "this is infrastructure, not a feature, and we're picks-and-shovels in the AI gold rush".

Anything below this line is execution. The decisions are made.
