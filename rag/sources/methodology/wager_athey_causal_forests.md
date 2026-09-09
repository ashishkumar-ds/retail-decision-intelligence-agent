---
source_type: methodology
title: Estimation and Inference of Heterogeneous Treatment Effects using Random Forests
url: Wager & Athey, Journal of the American Statistical Association 113 (2018); open copies e.g. arXiv 1510.04342
author: Stefan Wager & Susan Athey
year: 2018
license: journal article; open-access copies widely hosted
tier: tier-2
---

# Causal forests for heterogeneous treatment effects

## Motivation

An average treatment effect hides who responds. Causal forests use honest
(separately sampled) trees to estimate conditional average treatment effects
under unconfoundedness, providing pointwise-consistent estimates and
asymptotically Gaussian sampling distributions for valid confidence
intervals.

## Why it matters for targeting

Instead of a single uplift, the method supports store- or cohort-level
treatment effects with quantified uncertainty. This is the statistical basis
for deciding which segments will benefit most from a campaign action rather
than assuming one effect for everyone.

## Application in this project

uplift_targeting.md names causal forests as the standard for heterogeneous
effect estimation. This source grounds the inference claims (honest
splitting, confidence intervals) that a "why" explanation may reference when
justifying store-level treatment-effect heterogeneity.