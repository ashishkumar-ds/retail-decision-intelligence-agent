---
source_type: playbook
title: What Would Change This Decision
url: in-repo note (authored for this project)
author: this project / this repo
year: 2026
license: in-repo playbook note (authored for this project)
tier: tier-2
---
# What Would Change This Decision

The decision is deterministic: same inputs, same recommendation. So "what
would change this decision" has a precise answer — change one of the inputs
below and the recommendation can change. This document lists the real inputs
of the v2 policy (`v2-tabular-1`), not generalities.

## The four inputs that bucket every store

The policy places each store into a cell built from four features, then picks
the action with the best learned expected margin for that cell:

1. **Health tier** — below 40 is "low", 40 to below 70 is "mid", 70 and above
   is "high". The store health score crossing either boundary can flip the
   cell, and with it the action ranking.
2. **Velocity sign** — velocity at or below zero is "non_positive"; above
   zero is "positive". A store whose trajectory crosses zero changes cells.
3. **Margin tier** — below 15% is "thin", 15% to below 25% is "mid", 25% and
   above is "rich". Missing margin data is *assumed* mid (0.25): a real
   margin reading that lands thin or rich changes the decision basis.
4. **Availability** — POSSIBLE_SUPPLY_GAP, DEMAND, or UNKNOWN. Moving out of
   UNKNOWN (real demand/supply data arriving) changes the cell.

## When the learned values change

The per-cell action values are mean realised margins from decided history,
with a backoff: a cell needs at least 3 samples per action (MIN_CELL_N = 3)
to use its own mean; thinner cells fall back to the global means. So the
decision can change two ways without the store changing at all — new decided
outcomes (measured uplift from similar stores) shift the cell's or the
global means, and a cell crossing the 3-sample threshold switches from
backoff to its own evidence.

## When the evidence changes

- **The forecast signal arrives or resolves.** A record made with "no
  forecast signal" or a delayed feed is a decision on partial inputs; the
  signal appearing changes the evidence the next record is built from.
- **The window moves.** The 60-day recovery window's elapsed/remaining days
  change what "extend" or "pause" can realistically achieve.
- **New measured outcomes.** The measured uplift against matched controls is
  the system's learning signal; it enters through decided records, which is
  why deciding is itself an input to every future decision.

## What cannot change the decision

An LLM suggestion cannot change the decision — it is advisory, structurally
inert, and recorded nowhere. Only the inputs above, entering a new sweep and
a new record, move the recommendation. The human approval gate re-runs at
decision time regardless.
