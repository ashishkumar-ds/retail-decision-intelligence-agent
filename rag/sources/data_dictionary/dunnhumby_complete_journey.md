---
source_type: data_dictionary
title: Dataset Dictionary — dunnhumby The Complete Journey
url: in-repo note over the shipped CSVs / Kaggle mirror
author: dunnhumby (dataset); field map authored for this project
year: 2018
license: dunnhumby The Complete Journey dataset — terms restrict commercial redistribution; in-repo summary note
tier: tier-2
---
# Dataset Dictionary — dunnhumby The Complete Journey (fields used)

## Identifiers and time

- household_key: anonymous household identifier (2,500 households).
- DAY: day index of the transaction, 1..711 (two retail years, 2017-2018
  in this project's mapping; day 1 is 2017-01-01, day 366 is 2018-01-01).
- WEEK_NO: week index derived from DAY.
- BASKET_ID: one basket (checkout) identifier.
- STORE_ID: store identifier (582 stores).

## Monetary and promotional fields

- SALES_VALUE: transaction sales value; the primary metric throughout the
  pipeline (per-store daily means, baselines, lift).
- QUANTITY: units purchased; extreme values are outliers and are filtered
  upstream.
- RETAIL_DISC, COUPON_DISC, COUPON_MATCH_DISC: retailer and manufacturer
  discount components; used in cleaning filters.

## Campaigns

- campaign_table: household-to-campaign assignment (30 campaigns).
- campaign_desc: campaign start/end days; Campaign 18 runs days 587-642
  (56 days), the intervention window used across the series.
- coupon_redempt: redemptions; stores with any Campaign 18 redemption are
  excluded from control pools in the DiD analysis.

## Derived features in this pipeline

- RFM segmentation (recency, frequency, monetary) labels customers; the
  "Best Customers" segment drives the RETARGET_SEGMENT diversified action.
- Daily store features (dow, month, weekend flag, lag/rolling sales)
  feed the LightGBM forecast model served by the forecast API.
