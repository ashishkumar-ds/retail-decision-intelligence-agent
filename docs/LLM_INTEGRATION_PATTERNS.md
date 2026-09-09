# LLM Integration Patterns — Guide (from anthropics/commerce-agents analysis)

> **Purpose:** reference checklist to consult when adding LLM / agentic capabilities
> to this project. Distilled from a review of Anthropic's
> [`commerce-agents`](https://github.com/anthropics/commerce-agents) reference
> blueprint, mapped onto our Retail Decision Intelligence Agent.
>
> **Core principle (unchanged):** the LLM *explains*; the deterministic engine
> *decides*. The deterministic engine remains the executable safety boundary
> (see `docs/PROJECT_BLUEPRINT.md` §9). Human approval stays mandatory for all
> approval-required recommendations.

---

## Adopt now (before/while adding LLM work)

1. **`enable_*` config switches + stubbed methods.**
   A capability we lack (or want off) is a config flag that removes its
   tools/endpoints/grounding everywhere; a stubbed method returns an
   "unavailable" result without changing any other behavior or prompt bytes.
   Candidates: `RAG_ENABLED`, `ACTUALS_FEEDBACK_ENABLED`, `PHASE2_ENABLED`.
   This is what makes demo/staging "pilot mode" work.

2. **Pydantic schemas for every contract.**
   Convert `phase2/contracts.py` records (`InterventionRecord`,
   `OutcomeObservation`, `RecommendationRecord`, `ApprovalRecord`, ...) and the
   strict audit HTTP contract (4-field) to pydantic models with runtime
   validation — the same guarantee their "provenance gates" rely on.

3. **ruff + a `scripts/check.py` consistency gate in CI.**
   Their CI verifies invariants automatically. Our equivalents:
   - every recommendation in `guardrails/APPROVAL_REQUIRED_RECOMMENDATIONS`
     is emittable by some scorer path (and vice versa);
   - `decision_engine/planner.py` `PLANS` / `STEP_DESCRIPTIONS` keys never drift;
   - the RAG corpus rebuild is reproducible from in-repo sources.

## When adding the LLM layer (follow these patterns)

4. **One model, one turn loop, gates in code — not in prose.**
   Rules live in code wherever a consequence is material; prompts describe,
   code enforces.

5. **Byte-stable prompt + tools, fenced breakpoint for per-request data.**
   The static prompt and `tools[]` are identical bytes every turn (prompt-cache
   friendly, predictable); per-request data goes in a fenced block after a
   breakpoint. A change to prompt/tool/skill text must re-derive any derived
   artifact (their `scripts/check.py` enforces this).

6. **One home per rule.**
   A rule goes in a tool description, the prompt, or a flow/skill doc *by how
   often it applies* — never duplicated across homes.

7. **Typed backend interface, not raw tools.**
   Wrap `tools/forecast_tool.py` / `tools/campaign_tool.py` behind a typed
   interface (like their `StorefrontBackend` / `MerchantBackend`); the model
   reads only results, never raw HTTP. Fixed-order flows enforce order in the
   backend method, not the prompt.

8. **Declarative flow specs (`SKILL.md` style).**
   Describe each decision flow (`standard`, `near_deadline`,
   `feedback_loop_redecision`, ...) in small Markdown specs the planner reads,
   instead of hardcoded `PLANS` dicts. Enables "start small": implement two
   flows, stub the rest.

9. **Untrusted content is fenced data; retrieval is context, never authority.**
   RAG output may inform phrasing/context; it must never override a
   deterministic result or rule threshold.

## Later / optional

10. **Presentation surface** — a minimal "recommendation card" (score, evidence,
    confidence, approval status) for managers, mirroring their
    `PresentationExtension` idea.
11. **Event-shaped logging with cache metrics** — when LLM calls exist, log a
    `turn_complete`-style event including `cache_read_input_tokens` from day
    one to verify prompt caching.

## Do NOT copy

- Conversation-centric turn loop (we are a scheduled sweep + API service).
- Their commerce domain (cart, checkout, listings) — irrelevant to us.
- Multi-runtime packaging (Messages API + SDK + Managed Agents) — start with one
  runtime; add others only when actually needed.
- Next.js web apps.

---

*Source analysis: comparison of `anthropics/commerce-agents` architecture
(fictional ACME data, Apache-2.0) against this repo, conducted in the v4 review
session. Their repo is a reference blueprint — follow the patterns, don't copy
the code.*
