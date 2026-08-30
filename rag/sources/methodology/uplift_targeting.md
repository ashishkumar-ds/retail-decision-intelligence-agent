# Uplift Targeting and Heterogeneous Treatment Effects

## Why average effects are not enough

An average campaign effect hides who actually responded. Targeting by
predicted response (a propensity-style model) can pick customers who would
have bought anyway; targeting should use predicted uplift — the difference
between outcomes with and without the action.

## Standard methodology

- QINI / uplift curves (Radcliffe & Surry, 2011): rank individuals by
  predicted uplift and measure incremental gains against a randomized
  control group.
- Meta-learners (Kuenzel et al., PNAS 2019): T-learner, S-learner and
  X-learner estimate conditional treatment effects from observational or
  experimental panels.
- Causal forests (Wager & Athey, JASA 2018): nonparametric heterogeneous
  effect estimates with honest splitting.

## Application in this project

The scorer's health score and recovery velocity stratify stores; the
diversified policy (retarget segment, timing shift, reallocate budget) is a
discrete uplift action set. Every uplift claim must be backed by an
evaluated intervention with matched-control evidence; the budget allocator
scores candidates by expected lift times confidence but only admits stores
with SUFFICIENT outcome evidence.
