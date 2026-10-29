---
source_type: playbook
title: Do-Nothing Risk Playbook
url: in-repo note (authored for this project)
author: this project / this repo
year: 2026
license: in-repo playbook note (authored for this project)
tier: tier-2
---
# Do-Nothing Risk Playbook

What "doing nothing" means in this system, and why the risk is real. This
playbook is qualitative by design: it names the mechanisms, never invents
numbers. Every number an answer quotes must come from the store's own record
(the numeric grounding guard enforces this).

## What "do nothing" is here

Declining to decide is not one of the engine's actions — the action set is
CONTINUE, MONITOR, EXTEND_INTERVENTION, RETARGET_SEGMENT, TIMING_SHIFT,
PAUSE_INTERVENTION, ESCALATE. A store left pending is a store whose
recommendation is never recorded and whose intervention never runs. "Do
nothing" therefore means: the current trajectory continues unmeasured and
unmanaged.

## The recovery window is the clock

The engine reasons over a 60-day recovery window. Every day a store spends
pending is a day of that window spent without intervention. A store deep into
its window (many days elapsed, few remaining) has less left to recover with —
the "days left" figure on the record is the honest size of the remaining
opportunity, and it only ever shrinks.

## The baseline erodes quietly

The engine compares the baseline forecast to the current trajectory. Doing
nothing does not freeze the baseline: if the trajectory that triggered the
recommendation continues, the gap between where the store was headed and
where the baseline assumed it would be widens. The risk of doing nothing is
not "zero change" — it is the unmanaged continuation of whatever trajectory
earned the recommendation in the first place.

## Unmeasured interventions are lost knowledge

The policy learns from realised outcomes (measured uplift against matched
controls). A store whose intervention never runs contributes no outcome, so
the next store like it is decided with the same ignorance. Doing nothing on
one store quietly taxes the accuracy of every future decision like it.

## Thin-margin and low-health stores have less slack

The policy buckets stores by health tier (below 40 / 40–70 / 70 and above),
velocity sign, and margin tier (below 15% thin / 15–25% mid / 25%+ rich). A
low-health, thin-margin store has the least slack to absorb continued
underperformance: the same percentage decline costs it proportionally more,
and the time it can survive without a decision is shortest. Where the record
lacks a real margin rate, the policy assumes the mid tier — the risk of doing
nothing on an assumed margin is itself a reason to verify the inputs first.

## Saying it honestly

An answer to "what is the risk if we do nothing?" that cannot ground a number
in the store's record should name the mechanism (window burn-down, baseline
erosion, lost learning) and say plainly which inputs the record lacks. A
specific risk with a missing input named is worth more than a precise-sounding
number that does not exist.
