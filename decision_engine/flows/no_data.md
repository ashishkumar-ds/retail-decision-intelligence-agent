# Flow: no_data route

## When to apply
The router emitted `no_data`: no forecast signal is available for the store
(cold start, missing metadata, or the forecast service has no record).

## Steps (executable keys, in order)
1. `flag_for_review` — short-circuit; scoring is skipped because there is
   nothing to score.

## Rules
- Missing data is a typed limitation (`tools/forecast_tool.DataLimitation`),
  never a backfilled zero or a guessed figure.
- The store is flagged for mandatory analyst review; no recommendation is
  produced and nothing enters the approval queue.

## Never
- Invent a baseline, extrapolate from other stores, or suppress the flag.
