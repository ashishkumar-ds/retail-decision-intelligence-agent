---
source_type: methodology
title: Parallel trends and DID identification (DID vs lagged-dependent-variable)
url: https://statmodeling.stat.columbia.edu/wp-content/uploads/2018/12/DID_LDV_2018nov.pdf
author: public hosted copy of the reference paper (see PDF for authorship)
year: 2018
license: public PDF copy
tier: tier-2
---

# Parallel trends and DID identification

## The parallel-trends assumption

Difference-in-differences identifies the treatment effect only under a
parallel-trends assumption: absent treatment, treated and control groups
would have followed the same trend (possibly conditional on covariates).
Pre-period trend checks on well-matched controls are therefore a prerequisite
for treating a DiD estimate as decision-grade (see did_matched_controls.md).

## Bracketing DID against adjustment estimators

The reference notes a bracketing relationship between difference-in-
differences and lagged-dependent-variable adjustment: the two estimators can
lie on opposite sides of the true effect under different assumption
violations. Treat any single adjustment strategy's estimate with caution
rather than as an exact, assumption-free answer.

## Retail application

Use this source to explain why the system checks pre-trends and matched
controls before claiming incremental impact, and why a raw before/after
comparison or a single estimator should not be over-trusted in the presence
of retail seasonality and drift.