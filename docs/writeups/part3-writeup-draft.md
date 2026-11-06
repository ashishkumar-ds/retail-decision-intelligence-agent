# Retail Decision Intelligence Agent

<!-- Notion upload map (delete after uploading):
Image under System Architecture  → docs/images/architecture.png (on main)
Image under Decision Orchestration → ~/Downloads/layer-4-decision-guardrails.png
Image under Stakeholder View     → docs/images/board-live-2026-11-03.png (on main)
-->

# Project Overview

This project completes the retail intelligence series by adding the decision layer on top of Part 2's automated execution. Using **FastAPI, Python, Supabase**, and an optional **Groq LLM** layer, the agent recommends one next action per underperforming store with its evidence, checks it against causal guardrails, routes budget-moving actions through human approval, and measures each outcome to improve the next decision. Unlike typical LLM agents, the decision path is pure code — every recommendation is recomputable by hand, citable by ID, and approved by a human before any budget moves.

## Problem Statement

Part 2 automated executing proven campaigns across the portfolio, but choosing a store's next action still relied on analyst judgment applied store by store. Decisions were inconsistent, uncited, and had no causal check or spend control.

> How can next-step decisions for underperforming stores be made deterministically, grounded in causal evidence, and kept under human control?
> 

## Objectives

- Recommend one next action per store with its evidence
- Keep every decision deterministic and recomputable
- Gate budget-moving actions behind human approval
- Measure each outcome and feed it into the next decision round

## My Role

As an AI engineer, I built the deterministic decision engine, causal guardrails, the fail-closed LLM explanation layer, the append-only approval ledger, and the evaluation harness that gates any behavior change in CI.

# Methodology

### System Architecture

Implemented an end-to-end architecture that separates deciding code from explaining code:

- **FastAPI decision layer** for deterministic decision making
- **Guardrails and approval layer** enforced fail-closed in code
- **Groq LLM layer** for grounded explanations (off-path, fail-closed)
- **Supabase (Postgres)** for durable approval storage
- **Render** for API hosting and **Vercel** for the stakeholder board

!architecture.png

<u>*Figure 1: System architecture connecting decision logic, causal guardrails, and the human gate. The LLM sits off-path and only explains.*</u>

<u>*Note: GitHub here hosts the static source datasets used to seed the system — store signals come from the shared forecast and campaign-audit APIs, not a continuously-updated pipeline.*</u>

### Decision Orchestration

Designed a fixed five-stage pipeline — route, plan, score, verify, approval gate — for every store, which enabled:

- **Deterministic scoring** from recovery %, velocity, and health score — no model calls on the decision path
- **Closed 9-label vocabulary** — e.g. `CONTINUE`, `EXTEND_INTERVENTION`, `REALLOCATE_BUDGET`
- **Verifier re-checks** of label, confidence, reason, and approval flag
- **Decision trajectory** recording every stage's outcome for audit
- **Causal spend gate** — scale-up and every spend move require a CONFIRMED matched-control DiD
- **Human double gate** — guardrails and verifier re-run at decision time and again at approve/reject

!1000062101.jpg

<u>*Figure 2: Decision pipeline with the causal gate blocking unsupported spend and the human gate on risky actions.*</u>

### Execution Outputs

#### Recommendation Output

Captured a complete, well-formed decision for every store after each run:

- **Recommended action** with the commitment it implies
- **Cited evidence** behind every number in the recommendation
- **Confidence and decision basis** with the stage-by-stage trajectory
- **Approval requirement and risk tier** — reversible, cautious, or irreversible
- **Choice pair and cost of inaction** so the reviewer reacts to a well-formed decision
- **Append-only audit record** with the authenticated principal

#### Stakeholder View

Generated a live board to keep stakeholders aligned with the decision loop:

- **Stores waiting** grouped by approval bucket for review triage
- **Recommended action, alternative, and commitment** on each store card
- **Token-gated approve** directly from the board
- **Attention queue tiers** flagging where human attention matters most
- **Grounded, cited explanations** for every store
- **Append-only decision log** for the raw audit trail

!1000061302.jpg

# Business Impact

The decision layer replaces analyst-by-analyst judgment with deterministic, evidence-grounded, human-gated decisions that scale across the portfolio.

| **Focus Area** | **Before (analyst judgment per store)** | **After (decision agent)** | **Impact Statement** |
| --- | --- | --- | --- |
| **Decision process** | Ad hoc, per-store analyst review | Fixed pipeline, recomputable by hand | Consistent decisions across 85+ stores |
| **Evidence** | Uncited analyst notes | Citable evidence IDs on every recommendation | Fully auditable decisions |
| **Causal check** | Own-baseline lift treated as campaign effect | Matched-control DiD gates scale-up and all spend moves | No unsupported budget moves |
| **Spend control** | No enforcement mechanism | Human double gate, fail-closed auth, dry-run default | Budget moves only through human decision |
| **Behavior control** | Unversioned logic changes | 23 pinned golden scenarios gate CI | Every change explicitly revalidated |

<u>*Note: impact statements are read from the system's audit records and CI evaluation gates — not benchmarks.*</u>

# What I'd Build Next

- **Randomized experiment engine** — the current DiD prior's CI admits zero, so causal evidence can only *block* spend today; randomizing rollouts across stores would generate evidence strong enough to *unlock* it.

# Conclusion

This project demonstrates how agentic retail systems can be built where the model explains and the code decides. By pairing deterministic scoring with causal guardrails and a structural human gate, the solution turns store recovery from analyst intuition into an auditable, measurable loop — one store, one recommendation, one approval, one measured outcome at a time.

<aside>
<img src="/icons/arrow-right_gray.svg" alt="/icons/arrow-right_gray.svg" width="40px" />

For a detailed walkthrough of the implementation visit the <a href="https://github.com/ashishkumar-ds/retail-decision-intelligence-agent">GitHub Repository</a>, the live <a href="https://retail-decision-intelligence-agent.vercel.app/">Stakeholder Board</a>, and the API <a href="https://retail-decision-intelligence-agent.onrender.com/health">on Render</a>.

</aside>
