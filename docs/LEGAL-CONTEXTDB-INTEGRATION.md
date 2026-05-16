# Legal Risk — Incorporating ContextDB Patterns into Company-Brain

> **NOT LEGAL ADVICE.** Claude is not a lawyer. This is a structured risk landscape so you know what questions to bring to actual counsel before shipping any of these. The law here is unambiguous in most places (open-source license interpretation has decades of case law) but specific facts always matter — the cost of a 30-minute IP-attorney call is much lower than the cost of being wrong.

---

## TL;DR

**Almost zero legal risk** if we incorporate ContextDB's IDEAS and ARCHITECTURAL PATTERNS into our own code. **Real risk** only arises if we (a) literally copy-paste their source files without preserving Apache 2.0 attribution, (b) use their NAME or trademarks in our marketing, (c) republish derivative-works they've designated, or (d) claim their published benchmarks as our own.

The recommendations in `COMPETITIVE-CONTEXTDB.md` are almost all idea-level (RRF fusion, memory tier split, PII layer pattern, evolution background process, integrations strategy). **Ideas and architectural patterns are NOT copyrightable** under US, EU, and most other jurisdictions. Looking at someone's open-source code to learn how they solved a problem and then writing your own implementation is what every engineer does, every day, legally.

The license they ship under (Apache 2.0) is **the most permissive copyleft-free license commonly used**: explicitly allows commercial use, modification, distribution, and patent grant. Even verbatim code copying is allowed if attribution is preserved.

**The two biggest practical risks** to actually watch:
1. A junior engineer copy-pastes a `contextdb/...py` file's contents into our codebase without preserving the Apache 2.0 NOTICE — license violation, easy to fix, embarrassing if discovered publicly.
2. We claim "inspired by ContextDB" in marketing without attribution OR claim we invented patterns they pioneered (intellectual-honesty issue, not a legal one, but it'd burn the relationship recommended in the competitive brief).

---

## License analysis — what Apache 2.0 actually grants and requires

ContextDB ships under **Apache License 2.0** ([LICENSE file in their repo](https://github.com/atomsai/contextdb/blob/main/LICENSE)). Key clauses:

| Clause | What it means for us |
|---|---|
| **Section 2 — Grant of Copyright License** | We can reproduce, prepare derivatives, distribute, sublicense, and sell. **Yes**, including in proprietary commercial software. |
| **Section 3 — Grant of Patent License** | If they have patents on anything in the code, those patents are licensed to us perpetually, royalty-free. (Big deal — protects us from a "we open-sourced it then sued you" trap.) |
| **Section 4(a) — Notice retention** | If we DISTRIBUTE their code (modified or not), we must retain copyright/patent/trademark/attribution notices in the source files. |
| **Section 4(b) — Modification marking** | If we modify their files, we must add a "modified by" notice. |
| **Section 4(c) — NOTICE file** | If they ship a `NOTICE` file (they don't — verified), we'd have to preserve it. |
| **Section 4(d) — Original license inclusion** | The Apache 2.0 license text must travel with redistributed code. |
| **Section 5 — Submission of Contributions** | Doesn't apply unless we contribute back to ContextDB. |
| **Section 6 — Trademarks** | We can NOT use their trademarks (the name "ContextDB", their logo) for endorsement / promotion of OUR product without permission. |
| **Sections 7-9 — Disclaimer / Liability / Indemnity** | Standard warranty disclaimers. |

**Critical implication**: Apache 2.0 is NOT GPL. We can take Apache code into our proprietary commercial codebase **without** opening up our own code. We just preserve their attribution in the files we copied.

---

## The copyright vs idea distinction (the doctrine that does most of the work)

US copyright law (17 U.S.C. § 102(b)) + every signatory of the Berne Convention:

> *"In no case does copyright protection for an original work of authorship extend to any **idea, procedure, process, system, method of operation, concept, principle, or discovery**, regardless of the form in which it is described, explained, illustrated, or embodied in such work."*

**Translation**: copyright protects EXPRESSION (the actual code as written). It does NOT protect IDEAS (what the code does, the algorithm, the architectural pattern, the design).

This is why:
- You can read the Linux kernel and write a Linux clone (Tanenbaum read Unix and wrote Minix)
- You can read VS Code and write Cursor (and Sublime Text and Zed and …)
- You can read pandas and write polars
- You can read Mem0/Letta/Mem-GPT and write your own agent memory system

What you CAN'T do:
- Copy `contextdb/dynamics/retrieval.py` line-by-line into our codebase as `companybrain/whatever/retrieval.py`
- Take a substantial portion verbatim and call it "transformative" (the smoke test is "would a programmer reading both say one is derived from the other?")

What you CAN do:
- Read their `retrieval.py` to understand RRF, then write our own `retrieval.py` that uses RRF
- Even if your independent implementation looks similar in shape, that's fine — RRF only has so many ways to be implemented

---

## Patent landscape — what to check

Reciprocal Rank Fusion (RRF) was published in **2009** by Cormack, Clarke, and Buettcher (University of Waterloo) in *"Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods"*. **Public academic prior art for 17 years** — almost certainly unpatentable, and even if filed, would have expired by 2029.

The "memory hierarchy" idea (working/short-term/long-term) is **cognitive science from 1972** (Tulving's episodic vs semantic memory). Decades-old prior art.

PII detection patterns are **NIST-published standards** (SP 800-122). Not patentable.

Hash-chain audit logs are **how Bitcoin's blockchain works** — Satoshi 2008 paper. Decades of prior art.

Background processes for index maintenance are **decades-old database patterns**.

**My non-lawyer read**: there is **no plausible patent** on any of the patterns in COMPETITIVE-CONTEXTDB.md's recommendations. ContextDB is also single-maintainer + bootstrapped + Apache-licensed — they have not signalled any IP-aggressive posture.

But: **before shipping**, do a quick patent search at [Google Patents](https://patents.google.com) on "reciprocal rank fusion", "agent memory hierarchy", "PII detection embedding" to confirm there's no surprise.

---

## Per-recommendation legal risk

For each item from `COMPETITIVE-CONTEXTDB.md`, the actual risk:

### #1 — Multi-graph RRF fusion (2 days, before seed demo)

**Plan**: read their `dynamics/retrieval.py`, understand the RRF algorithm + how they compose graphs, then write `assembly/multi_graph_retrieval.py` from scratch.

| Risk | Score | Why |
|---|---|---|
| Copyright | **Zero** | RRF is public-domain academic algorithm; we write our own code |
| Patent | **Zero** | 17 years of academic prior art; no plausible patent |
| License | **Zero** | We're not redistributing their code |
| Trademark | **Zero** | We're not using their name |
| Attribution | **Optional but recommended** | Cite RRF paper and "inspired by ContextDB" in code comments |

**What to do**: implement freely. In the code add a one-line comment "RRF fusion (Cormack et al. 2009; pattern adapted from ContextDB)".

### #2 — PII + audit + typed TTLs (4 days, ADR-0064)

**Plan**: ship a privacy/audit layer using the SAME PATTERNS but our own implementation. Use Microsoft Presidio for PII detection (Apache 2.0 by Microsoft) or write our own regex catalog.

| Risk | Score | Why |
|---|---|---|
| Copyright | **Zero** | NIST-published standards; well-known patterns; our own code |
| Patent | **Zero** | PII detection + hash-chained audit are textbook compsci |
| License | **Zero** if we use Microsoft Presidio (Apache 2.0; preserve their attribution) | |
| Trademark | **Zero** | |
| Compliance | **Increases for us** (positive — it's the whole point) | We become MORE compliant, not less |

**What to do**: ship freely. If we lift literal code from Microsoft Presidio, preserve their Apache 2.0 NOTICE.

### #3 — ExperientialMemory tier (3 days)

**Plan**: add a third memory tier alongside our T0/T1/T2 zones to record query history + verifier corrections + extracted-pattern-utility outcomes.

| Risk | Score | Why |
|---|---|---|
| Copyright | **Zero** | This is just adding a database table + a write path; no code copied |
| Patent | **Zero** | "Logging past interactions" is not patentable; been done since the '70s |
| License | **Zero** | |
| Trademark | **Zero** | |

**What to do**: build freely. Don't even need to mention ContextDB in code.

### #4 — `companybrain-lite` SQLite-default distribution (1-2 weeks)

**Plan**: ship a single-package Python distro that uses SQLite + DuckDB instead of Postgres + Neo4j + Qdrant for low-friction developer eval.

| Risk | Score | Why |
|---|---|---|
| Copyright | **Zero** | We're picking different deps and writing thinner glue; not copying |
| Patent | **Zero** | "Use SQLite" is not a patentable design |
| License | **Zero** if we don't bundle their code | |
| Trademark | **Zero** | |
| Their reaction | **Possibly upset** if we explicitly position as "ContextDB but for code" — handle that with the complementary framing | |

**What to do**: ship freely. The framing in our marketing matters more than the legal status.

### #5 — `evolution.py` background process (auto-link / consolidate / prune) (2 days)

**Plan**: scheduled job that adds SIMILAR_TO edges, consolidates duplicate Pattern entities, prunes hallucinated entities after TTL.

| Risk | Score | Why |
|---|---|---|
| Copyright | **Zero** | Standard database maintenance pattern |
| Patent | **Zero** | Index maintenance, entity merging are textbook |

**What to do**: build freely.

### #6 — Native langchain/crewai/autogen integrations (4 days)

**Plan**: ship `from companybrain.langchain import BrainMemory` adapters that subclass each framework's memory interface and back it with our MCP server.

| Risk | Score | Why |
|---|---|---|
| Copyright | **Zero** | We write our own adapters; they implement public APIs |
| Patent | **Zero** | |
| License | **CHECK PER FRAMEWORK** | langchain (MIT), crewai (MIT), autogen (CC-BY-4.0 / MIT) — all permissive; subclassing their public abstractions is exactly what they're for |
| Trademark | **Be careful** | We can SAY "works with LangChain" but can't claim endorsement / use their logo without permission |

**What to do**: ship freely. Use each framework's name in docs as nominative fair use ("integrates with LangChain"), not as endorsement.

### #7 — Hermetic benchmarks (0.5 days)

**Plan**: build our own benchmark suite with our own fixtures.

| Risk | Score | Why |
|---|---|---|
| All categories | **Zero** | Our own measurements |
| **CAUTION**: do NOT reproduce their benchmark numbers as our own. Don't write "5ms p95 search" unless OUR system actually does it. False advertising. |

**What to do**: only publish numbers our system actually achieves. Verify before claiming.

---

## What we ABSOLUTELY MUST NOT do

1. **Copy their code verbatim into our repo without attribution.** Even one file. Even with renaming. Apache 2.0 requires attribution preservation. Violation = license violation = potential injunction + reputational harm. Easy to avoid; just write our own implementations.

2. **Use the name "ContextDB" or their logo in our marketing as endorsement.** "Better than ContextDB" — fine (comparative claim). "ContextDB recommends Company-Brain" — illegal unless they actually do.

3. **Republish their code as part of an open-source bundle without preserving their LICENSE + per-file copyright headers.** If `companybrain-lite` includes any of their code (which it shouldn't — we'd write our own), the bundle has to ship their license + attribution.

4. **Claim their published benchmarks as ours.** "We achieve 5ms p95" when we don't — false advertising under FTC rules + Apache 2.0 §6 (no endorsement implications).

5. **Reverse-engineer their proprietary models if they later release any.** They have an `rl_manager.py` referencing torch+trl. If they ship a TRAINED MODEL under a more restrictive license (not Apache), don't fine-tune from their checkpoint.

---

## What we SHOULD do as good practice (not strictly legal, but smart)

1. **Add a `THIRD-PARTY-INSPIRATIONS.md`** in our repo that lists ContextDB + a few patterns we adopted. Shows intellectual honesty + protects us if anyone ever raises the question. Suggested entry:

   ```markdown
   ## ContextDB (https://github.com/atomsai/contextdb, Apache 2.0)
   - The Working/Factual/Experiential memory tier split inspired our
     ExperientialMemory tier.
   - The PII-detection-before-embedder pattern inspired our privacy layer.
   - The Reciprocal Rank Fusion approach is a public-domain algorithm
     (Cormack et al. 2009) we both implement; our implementation is
     written from scratch.
   ```

2. **In code comments where we use a pattern they pioneered**, add a one-line citation. Example:

   ```python
   # MultiGraphRetrieval — fuses N graph rankings via Reciprocal Rank Fusion
   # (Cormack et al. 2009). Pattern of N-graph fusion adapted from ContextDB
   # (Apache 2.0). Our implementation is independent.
   ```

3. **Cold-email Gaurav** (already recommended in the competitive brief) BEFORE we ship a public release that touches these patterns. A friendly heads-up costs nothing and locks in the "complementary collaborators" relationship. Drafted email shape:

   > *"Gaurav — read your ContextDB code; impressed. We're building company-brain (codebase context for engineering orgs, MCP-served). Different market but adjacent architecture; we're inspired by your memory-tier split + RRF fusion pattern + PII layer approach and have our own implementations. Wanted to ping before our docs reference your work — happy to coordinate framing if you'd prefer specific language. We see ourselves as complementary; would love to write a joint blog post comparing the two if you're open."*

4. **If we ship `companybrain-lite` aimed at developers**, explicitly call out ContextDB in the README as adjacent / complementary. Reduces risk of being seen as a clone; positions us in the agent-memory community as collaborators.

5. **Document our license decision**. We're Apache-2.0 / MIT / commercial / dual? Settle this before any external code is incorporated. ContextDB is Apache 2.0 — easy to combine into any of these.

---

## The risks NOT from ContextDB you should also know about

This is broader than just contextdb but worth flagging since you asked about legal:

- **AI-generated code (this whole conversation included).** US Copyright Office (2023) ruled AI-generated content is NOT copyrightable; many other jurisdictions still figuring out. Practical implication: code Claude writes for you is probably yours to use, BUT you can't enforce copyright against someone who copies it. For a startup this is fine; for a company that needs strong IP positioning (acquisition, defensive patent), get a lawyer involved on the AI-code-attribution question.
- **Training data leakage.** If you fine-tune a model on customer code (we don't yet, but Product 4 might suggest it), data-residency + customer contracts will dictate what's allowed.
- **Anthropic / OpenAI ToS.** Whatever model powers our extraction has a ToS that restricts model output. Read the latest Anthropic terms before publishing extracted code as "our brain's output" — there are usually clauses about not using the output to train competing models.
- **Customer code seen in extraction.** When the brain extracts customer X's code, our infrastructure briefly handles their proprietary IP. Customer contracts must explicitly permit this. Standard MSA boilerplate; just make sure it's there before the first paid pilot.
- **EU AI Act (effective August 2026).** The brain qualifies as an "AI system"; needs documentation per Article 13. Compliance is in our roadmap (Product 3) but operational obligation kicks in regardless of whether we sell the product.

---

## When to call an actual lawyer

- **Before** any public release (pre-launch IP review, $1-3K)
- **Before** writing the FIRST customer contract (data handling, IP ownership of extracted entities, $5-10K for a defensible MSA template)
- **Before** any acquisition / acqui-hire conversations involving ContextDB or any other open-source incumbent
- **Before** filing any patents (defensive — keep ContextDB's patterns out of our patent claims)
- **Before** Series A — investor diligence checklist will include open-source compliance audit

A startup-friendly IP attorney on retainer at ~$2-3K/month is normal at seed stage. Worth it for the speed of decisions like this one.

---

## Bottom line

Of the 7 "steal" recommendations in COMPETITIVE-CONTEXTDB.md, **all 7 are legally clean** under Apache 2.0 + ideas-aren't-copyrightable doctrine, AS LONG AS:

1. We **write our own code** (not copy-paste theirs), AND
2. We **preserve attribution** for any literal snippets that do leak in (best practice: zero literal snippets), AND
3. We **don't use their name as endorsement** in marketing, AND
4. We **don't claim their numbers** as our own.

The real risk isn't legal — it's **reputational and relational**. The agent community is small; making an open-source incumbent feel ripped-off is a faster way to lose Series A investor warmth than any legal complication. The fix is the same fix recommended in the competitive brief: cold-email Gaurav, frame ourselves as complementary, attribute generously, ship as friendly neighbours not stealth competitors.

**One sentence for the founder**: "Apache 2.0 + ideas aren't copyrightable means we can take their playbook freely; preserve attribution for anything literal, talk to Gaurav like a colleague before we ship, and the legal exposure is essentially zero."

---

## Action items

1. [ ] **Decision needed**: confirm our own license model (Apache / MIT / commercial / dual) — affects how we receive any external code.
2. [ ] **Once before any external code touches our repo**: 30-min call with an IP attorney to confirm this risk read.
3. [ ] **Before shipping the RRF fusion**: add citation comment + entry in `THIRD-PARTY-INSPIRATIONS.md`.
4. [ ] **Before shipping ADR-0064 (privacy)**: confirm Microsoft Presidio license terms if we use it; otherwise write our own.
5. [ ] **Before public release of `companybrain-lite`**: have IP attorney review for open-source compliance.
6. [ ] **This week**: cold-email Gaurav at ContextDB. (Already in COMPETITIVE-CONTEXTDB.md action items — repeat here because it's the highest-leverage non-legal action.)
7. [ ] **Before first customer contract**: lawyer-drafted MSA covering: data handling, extracted-IP ownership, model-output usage, sub-processor disclosure (Anthropic).
8. [ ] **Before Series A**: open-source compliance audit (typically a $3-5K engagement; most VCs ask for it).
