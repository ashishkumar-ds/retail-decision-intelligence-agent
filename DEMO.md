# Stakeholder Demo — 5-minute walkthrough

The story in one sentence: **"A retail decision agent where the brain is
code, every number is citable, a human gates every budget move — and even a
hallucinating LLM cannot corrupt a decision."**

## Before the demo (one command)

```bash
bash scripts/seed_demo.sh
```

This starts the agent on :8001 and runs one fresh recommendation sweep.
Everything below assumes that server is running. All URLs work in a browser
or with `curl -s <url> | python3 -m json.tool`.

## The walkthrough (in this order — each step sets up the next)

### 1. System is alive and honest about what it is
`http://localhost:8001/health`
> "Feature flags are explicit: the deterministic engine is always on; the
> LLM layers are opt-in and fail closed."

### 2. The whole portfolio at a glance
`http://localhost:8001/ui`
> "85 stores scored every cycle. The dashboard is the buyer's first screen:
> 47 need intervention, 18 are mid-intervention, 20 are working well."

### 3. One decision, fully explained — the core of the pitch
`http://localhost:8001/why/317`
> "Every recommendation carries citations by ID — [rec:...] for decisions,
> [event:...] for measured outcomes. Any number in this narrative can be
> recomputed by hand from the evidence. That's the standard: explainable
> means recomputable, not 'the model said so'."

### 4. The agent helps — but never decides
`http://localhost:8001/advisory/317?question=should+we+extend+this+store`
> "The LLM drafts a triage suggestion for the reviewer. Note three
> structural facts: suggested actions come from a closed vocabulary, the
> note passes the same numeric-grounding guards as the narrative, and
> `auto_applied` is false — in code, not in a prompt."

### 5. Prove the LLM cannot lie
Pick any store and ask the advisory a leading question. If the model
invents a number or flips a sign (a reasoning model wrote "+38.9" for a
"-38.9" decline in our testing), the grounding guard rejects it and the
endpoint serves the engine's own answer, marked
`advisory_status: advisory-grounding-failed`. The system never errors and
never repeats the hallucination.
> "We run this on a free Groq model on purpose: the guardrails make cheap
> models safe. That's the skill — not calling an API, but making it safe."

### 6. Measure before you spend
`http://localhost:8001/simulate/306?started_day=531&window_days=56`
> "Before any budget moves, the agent replays the calibrated causal prior
> (DiD +2.84%, CI [-0.5, 6.2]) against the store's observed baseline. Raw
> lift is explicitly labeled NOT causal — the +9.7% market drift measured
> in Part 1 is exactly what raw lift wrongly credits to the intervention."

### 7. The human gate
`http://localhost:8001/ui/approvals`
Then (with the demo token, optional — skip live if short on time):
```bash
curl -X POST localhost:8001/approve/317 \
  -H "Authorization: Bearer demo-token-not-for-production"
```
> "Two gates: guardrails are checked when the recommendation is created AND
> when a human decides. Every decision lands in an append-only ledger with
> the gate result. Without the token configured, these endpoints refuse
> with 503 — fail closed."

### 8. The system grades itself
`http://localhost:8001/ui/eval-history`
> "22 golden business scenarios + 4 simulator evals run in CI against a
> pinned calibration. A decision-behavior change that doesn't update the
> golden cases in the same commit fails the build."

## Closing line

> "Part 1 proved what works (DiD analysis). Part 2 automated rollout
> (FastAPI + n8n). Part 3 — this system — decides what to do next, safely:
> deterministic brain, grounded explanations, LLM strictly above the gate,
> human above everything."

## Live-demo tips

- The tunnel URL for remote stakeholders: create with
  `cloudflared tunnel --url http://localhost:8001` (or deploy to Render for
  a permanent link).
- Have one pre-checked `/why/317` response in a browser tab as a fallback
  in case the free LLM is rate-limited mid-demo.
- If asked "why not let the LLM decide?": the decision repeats weekly
  across thousands of stores, is audited, and moves budget. Deterministic
  where it's repeatable; LLM where it's language; human where it's money.
