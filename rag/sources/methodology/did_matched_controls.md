# Difference-in-Differences and Matched Controls

## Why own-baseline lift is not causal

Comparing a store's post-intervention sales against its own pre-intervention
baseline measures change over time, not the intervention's effect. Any
market-wide movement — seasonality, holidays, promotions, inflation-driven
drift — is credited to the intervention. In this project's Campaign 18
validation, all-store sales drifted by roughly 9.7 percent during the
56-day campaign window, which inflated every single-series estimate.

## The DiD fix

Difference-in-differences contrasts treated units against untreated controls
over the same pre/post windows, so common shocks cancel out. The causal
effect estimate is: (treated post minus treated pre) minus (control post
minus control pre). References: Angrist & Pischke, Mostly Harmless
Econometrics (2009); Imbens & Rubin, Causal Inference for Statistics and
Policy (2015).

## Matched-control selection

Controls must be comparable: in this project, candidate control stores need
at least 80 percent day coverage over the study window and are matched on
pre-window sales level and intra-pre trend via z-scored k-nearest
neighbors. Matching on pre-period level plus trend controls for level
differences and regression-to-the-mean; the notebook notes that
matched-control selection can bias point estimates upward, so treat
matched estimates as an upper bound. See Imbens & Rubin (2015), matching
chapter.

## Decision rule

Raw own-baseline lift is an operational metric only. A scale-up decision
(extended spend, more stores) requires confirmed treated-versus-control
DiD lift at or above the target of 3 percent. Worked example from this
project: store 317 showed a positive own-baseline lift of about 9 percent,
but the matched-control DiD was strongly negative because controls
outperformed the treated store — the raw lift was market drift, and the
causal guardrail blocked scale-up. Caveat: observational DiD is not a
randomized experiment; treat confirmed DiD as decision-grade evidence with
stated assumptions, not experimental proof.
