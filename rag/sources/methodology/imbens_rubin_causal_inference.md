---
source_type: methodology
title: Causal Inference for Statistics, Social, and Biomedical Sciences
url: https://imai.fas.harvard.edu/research/files/ImbensRubin.pdf (excerpt); Cambridge University Press 2015
author: Guido W. Imbens & Donald B. Rubin
year: 2015
license: textbook excerpts via public PDF route; full text is copyrighted
tier: tier-2
---

# Potential outcomes and causal effect

## Association is not causation

A correlation or a pre/post change does not establish causation. Under the
potential-outcomes (Rubin causal model) framework, a causal effect is the
difference between the outcome under treatment and the outcome under control
for the same unit, made estimable through assignment and comparison of
comparable groups.

## Why a counterfactual is required

Observed outcomes alone cannot show what would have happened absent the
intervention. A valid comparison group — randomized or well-matched (see
did_matched_controls.md) — supplies the counterfactual. Without it,
seasonality, drift and selection bias can masquerade as campaign effect.

## Retail application

Use this source to justify why an observed uplift is not enough evidence for
a scale-up decision. In this project a raw own-baseline lift is an
operational metric only; a decision-grade causal claim requires treated
versus control comparison under the potential-outcomes framing, with stated
assumptions.