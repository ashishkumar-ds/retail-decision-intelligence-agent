---
version: 1
slug: "app-store-id-storedetail-tsx"
primary_target: "app/store/[id]/StoreDetail.tsx"
related_targets: []
---

# Surface brief — store detail page (`app/store/[id]/StoreDetail.tsx`)

**Scope.** The per-store "why" page: hero verdict, the narrative panel, the
evidence panel with verification and provenance, and (since 2026-10-28) the
"Ask about this store" advisory panel. Reached from board rows; one store per
visit.

**Audience & job.** A stakeholder reviewing one recovery decision: understand
why the engine recommends what it recommends, verify the evidence, and — new —
interrogate the record through the off-path LLM advisory before approving.

**Constraints.** Read-only GET surface except nothing (the approve POST lives
on the board only). Citation markers, methodology chunks and raw engine tokens
never print; `recommendation_id` is the one provenance exception. Advisory is
budget-capped and structurally inert; every degraded state answers in
sentences. dunnhumby system: Roboto body, Space Grotesk verdict figures,
restrained mint/teal/indigo palette, panel cards.

**Direction.** The ask panel is a third card under the evidence grid, in the
`.decision` family: tinted panel fill, uppercase teal kicker carrying the
honesty line ("Suggestion — not a decision, records nothing"), Space Grotesk
for the suggested action. Presets first (three grounded questions), free text
second, budget sentence always visible. No colored edge on the result card —
the kicker carries the accent (detector's AI-UI tell avoided).

**States.** idle → asking (disabled controls, "Reading the grounded evidence…")
→ done (honesty kicker, suggested action, optional note, translated status) or
error (429 rate-limited / 409 no grounded evidence / 503 layer off / network,
each a sentence, each saying nothing was recorded).

**Unresolved.** Whether a future board-level ask (one box for the whole board)
is ever wanted — deliberately out of scope; the advisory is per-store.
