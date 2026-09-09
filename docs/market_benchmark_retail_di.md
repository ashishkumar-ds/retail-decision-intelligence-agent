# Retail Decision Intelligence — Market Benchmark & Project Alignment

Research note (2026-08-30). Compares our 3-project Dunnhumby Decision
Intelligence series against how commercial platforms build retail DI, and
maps the gaps to our roadmap. Sources: hypersonix.ai (platform + whitepapers),
hyperfinity.ai (What Is Decision Intelligence / product pages), plus the
decision-intelligence lineage those sources cite (Gartner strategic-trend
classification; Pratt & Zangari 2008; Kozyrkov/Google's chief-decision-
scientist framing).

---

## 1. How the market builds retail DI

### Hypersonix ("ProfitGPT") — agentic, profit-first, closed loop
- **Positioning**: "Agentic AI platform that maximizes retail & eCommerce
  profitability." Explicitly anti-dashboard: *Detect → Act → Execute → Learn*
  as a continuous loop. Our feedback loop is the same shape.
- **5 unified autonomous agents**: Pricing AI, Competitor AI, Promo AI,
  Inventory AI, Forecasting AI — each owns a decision domain, all feed one
  profit objective.
- **Guardrailed autonomy**: "Executes with one click, **within set
  guardrails**" — human approval is a policy layer, not a bottleneck. This is
  exactly our `guardrails.APPROVAL_REQUIRED_RECOMMENDATIONS` +
  bearer-token approval endpoints pattern.
- **Explainability as a feature**: "Every decision comes with a clear 'why'."
  Our Priority 4 (grounded "why" RAG over the event log) is this capability —
  theirs is the market proof that it must be a first-class product surface.
- **Claimed outcomes**: 8–15% annualized profit lift; forecast accuracy +94%
  vs 60–65% baseline; 90 days to margin impact. Unverified vendor claims, but
  the *metric discipline* (profit, not accuracy) is the lesson.
- **Whitepaper frameworks**: "BI 3.0" (BI → decision automation), "Decision
  Intelligence 101" (BI is no longer sufficient), a six-pillar data &
  analytics framework, VUCA inventory management, enterprise-AI
  patterns/anti-patterns.

### HyperFinity — DI as "the commercial application of data science + AI"
- **Definition**: joined-up, customer-centric decision making across
  interlinked areas (assortment, pricing, marketing, merchandising, supply
  chain). Emphasizes **augmentation, not replacement**: "it's not about
  replacing humans with fully automated processes."
- **Data foundation**: modern data stack (warehouse-first), customer ×
  product × click data joined; democratized insight; consultancy + software
  with **"measure everything and prove real value"** (90-day value journeys,
  quantified opportunities like "£2.8M revenue opportunity").
- **Gartner citation**: DI named a top strategic technology trend (2022);
  >1/3 of large organizations adopting within two years — useful external
  validation for our architecture narrative.

### Cross-platform pattern (what "good" looks like)
1. **Closed loop**: sense → decide → act → measure → learn. Measurement is
   built in, not an afterthought.
2. **Causal honesty**: leaders separate correlation from causation when money
   moves (holdouts/matched controls); vendor case studies quote *profit
   lift*, not model accuracy.
3. **Human-gated autonomy**: agents act within guardrails; high-cost actions
   require approval; every action is logged and explainable.
4. **One profit objective** across agents; per-domain agents specialize.
5. **Explainability for business users**, grounded in the data trail.
6. **Value proving as a discipline**: fixed horizon (90 days), quantified

---

## 2. Where our series already matches

| Market pattern | Our implementation |
|---|---|
| Closed loop (sense→decide→act→measure→learn) | P2 audit → P3 Phase-2 registry → outcome evaluation → re-scoring (`/recommendations` carries `outcome_evidence`) |
| Guardrailed autonomy | Approval-gated action set + bearer-token approve/reject; fail-closed when no token configured |
| Causal measurement, not raw lift | Priority 3: `GET /controls/{store_id}` matched-control DiD; `scale_up_eligible` requires CONFIRMED DiD; store 317's +9.0% raw lift was DiD-refuted (−30.7%) |
| Deterministic decision core | Rule chain with recomputable health score + boundary-aware confidence; verifier gates persistence |
| Domain "agents" | Project-per-domain series: forecasting (P1), execution/campaign automation (P2), decisioning (P3) — with strict inter-service contracts |
| Event-sourced auditability | Append-only JSONL run log + event-sourced intervention registry; every decision reproducible by ID |

Genuinely differentiated vs both vendors: **our causal guardrail is
verifiable and conservative** (fail-closed, evidence-state discipline,
methodology published in the API response). Vendor pages claim profit lift;
none publish the measurement design. That is a strength to keep.

## 3. Gaps → roadmap adjustments

1. **Explanation surface (Priority 4 — confirmed as next).** Both vendors
   treat "why" as the product. Ours: grounded RAG over recommendation JSONL +
   registry events + outcome payloads; LLM only explains, citing record IDs;
   decisions stay deterministic.
2. **Single profit objective.** Hypersonix optimizes everything against
   profit. Our allocator scores `expected_lift × confidence`; add an explicit
   profit objective (expected incremental margin, net of campaign cost).
3. **Continuous monitoring ("Detects 24/7").** We evaluate on demand. Add a
   scheduled portfolio sweep (`/phase2/portfolio/evaluate` + checkpoint
   watcher) that emits outcome evaluations when windows close, instead of
   waiting for a caller.
4. **Simulation before action ("pre-launch simulations", Promo AI).** Our
   interventions execute then measure. Cheap addition: backtest mode that
   replays a candidate intervention against the P1 history + matched controls
   and returns predicted DiD before approval.
5. **Learning with every decision.** Their loop "gets smarter". Ours modulates
   confidence but doesn't learn strategy parameters. Priority 5's bandit
   experiment engine (epsilon-greedy/Thompson over the 85-store rollout) is
   the principled version — powered per P1 for ~+3% effects.
6. **Breadth of decision domains.** Vendors span pricing/inventory/assortment.
   We are deliberately campaign/store-recovery scoped with real dunnhumby
   data — keep the depth-over-breadth stance, but the intervention taxonomy
   (RETARGET_SEGMENT, TIMING_SHIFT, REALLOCATE_BUDGET) is the extension seam.

## 4. Positioning statement (for the README/report)

> Unlike dashboard-era BI and black-box agentic platforms, this series is a
> **decision intelligence agent with a provable measurement chain**: every
> recommendation is deterministic and reproducible, every intervention is
> event-sourced, every outcome is measured against matched controls with
> published methodology, and every scale-up decision is gated by causal
> evidence under human approval guardrails.

## 5. Quantexa — the "contextual fabric" school (added 2026-08-30)

Source: quantexa.com blog "Closing the Decision Gap: Core Capabilities of a
True Decision Intelligence Platform" (Dan Higgins, updated 2025-11-21).

Thesis: "You cannot have true decision intelligence without first solving
the data problem." The decision gap = duplicated, fragmented, siloed data
delivering hindsight instead of decision-time intelligence.

Their build: (1) contextual data foundation — entity resolution over
people/parties/things into a "contextual fabric" / knowledge graph;
(2) graph/network analytics so decisions score network context, not just
record attributes; (3) composite AI orchestrated per decision; (4) explicit
Automate / Augment / Support spectrum calibrating human-AI collaboration
per decision type; (5) three unified stages — modeling, execution,
monitoring; (6) modular composable services with API/batch/streaming;
(7) traceability as a hard requirement ("every decision is traceable,
every outcome explainable").

Mapping to our series: strong match on the modeling/execution/monitoring
unification (P1/P2/P3), traceability (event sourcing + grounded /why with
provable numeric grounding), human-AI calibration (approval-gated fail-closed
guardrails), and composite-AI composition. Gaps we consciously accept:
entity resolution/graph context (unnecessary for our single-source dataset,
but a Quantexa-style upgrade would feed network context — e.g. matched
control-group performance, neighboring stores' outcomes — into scoring;
candidate after Priority 5), streaming ingestion, and continuous monitoring
(already on the roadmap as the scheduled portfolio sweep).

Takeaway: Quantexa and Hypersonix agree from opposite directions — Quantexa
builds up from trusted contextual data, Hypersonix builds out from agentic
actions. Our series sits on the same spine: deterministic decision core +
event-sourced measurement + guarded autonomy, with the data problem solved
by contract-first ingestion (typed clients, fail-closed envelope validation)
rather than entity resolution, because our inputs are already entity-clean.

Sources consulted: hypersonix.ai (homepage, /whitepapers);
hyperfinity.ai (/what-is-decision-intelligence, /decision-intelligence);
quantexa.com (/blog/core-capabilities-of-a-true-decision-intelligence-platform);
DI lineage referenced therein (Gartner DI trend; Pratt & Zangari 2008;
Kozyrkov). Vendor outcome figures are unverified marketing claims and are
used only as capability signals, not benchmarks.
