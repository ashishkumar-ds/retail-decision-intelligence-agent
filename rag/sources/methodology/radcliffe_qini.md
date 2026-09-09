---
source_type: methodology
title: Quality measures for uplift models (Qini)
url: https://www.stochasticsolutions.com/pdf/kdd2011late.pdf
author: Nicholas J. Radcliffe & Patrick D. Surry
year: 2011
license: public PDF (KDD 2011 industrial track late-breaking result)
tier: tier-2
---

# Quality measures for uplift models

## Group-based uplift evaluation

Uplift quality cannot be judged pointwise: the counterfactual truth for a
single unit is never observed. It is judged on treatment versus control
groups, comparing incremental response within ordered segments. A model is
better if, as the targeted fraction expands, realised incremental gain grows
faster relative to a randomized control group.

## Qini and incremental gains curves

The Qini coefficient and the area under the incremental gains curve summarise
ranking quality for an uplift model. They measure whether ordering customers
by predicted uplift converts into realised incremental gains compared with
random targeting.

## Why not response accuracy

Conventional accuracy on observed outcomes rewards models that target likely
buyers; such a model can look strong on response while performing poorly on
true incrementality. Ranking by predicted incremental benefit is the metric
that matches the campaign objective.

## Application in this project

Supply explanation phrasing for why an uplift targeting decision is judged on
treatment/control segments and cutoffs, and why the budget allocator scores
candidates by expected lift times confidence rather than by forecast demand
alone.