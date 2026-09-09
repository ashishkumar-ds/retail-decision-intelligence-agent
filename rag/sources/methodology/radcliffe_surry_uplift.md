---
source_type: methodology
title: Real-World Uplift Modelling with Significance-Based Uplift Trees
url: https://www.stochasticsolutions.com/pdf/sig-based-up-trees.pdf
author: Nicholas J. Radcliffe & Patrick D. Surry
year: 2011
license: white paper, public PDF (Stochastic Solutions)
tier: tier-2
---

# Uplift modeling: response vs incremental gain

## Why response modeling is not targeting

A response model predicts the probability that a customer buys given the
treatment; an uplift model predicts the incremental difference between the
treated and untreated outcome for the same customer. Targeting the most
likely-to-buy customers can waste budget on people who would have purchased
anyway and can miss customers with negative marginal effect. When the
business objective is extra (incremental) sales, the target should be
estimated incremental impact, not raw propensity.

## The role of control groups

Incrementality cannot be observed for an individual; it requires a valid
control group of units that did not receive the treatment, so treated and
control outcomes can be compared under the same market conditions. Without a
control or randomized design, the apparent response to a campaign is
confounded by who was selected.

## Evaluation: incremental gains curves and Qini

Uplift models are ranked by predicted incremental effect and evaluated with
incremental gains curves and Qini-style coefficients against a randomized
control group. These rank-based measures summarise how well a model orders
customers by expected incremental benefit across targeting cutoffs.

## Application in this project

Human-gated recommendations should be driven by incremental effect, not raw
propensity (see uplift_targeting.md). A store or cohort is worth targeting
because its expected incremental response exceeds the control baseline, not
because it is likely to buy. See radcliffe_qini.md for the quality measures
used to evaluate such rankings.