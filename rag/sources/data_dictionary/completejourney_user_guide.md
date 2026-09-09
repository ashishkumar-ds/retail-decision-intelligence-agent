---
source_type: data_dictionary
title: The Complete Journey User Guide (completejourney R package)
url: https://bradleyboehmke.github.io/completejourney/articles/completejourney.html
author: Brad Boehmke, Steven M. Mortimer
year: 2025
license: package documentation, public web page
tier: tier-2
---

# The Complete Journey user guide (completejourney package)

## Source

The public `completejourney` R package exposes dunnhumby's The Complete
Journey: one year of household-level transactions from 2,469 households who
are frequent shoppers at a grocery store. It contains all purchases per
household, plus demographics and direct-marketing contact history for some
households.

## Tables

The package ships eight data sets: campaigns, campaign_descriptions, coupons,
coupon_redemptions, demographics, products, promotions_sample and
transactions_sample. The full transactions and promotions tables are
available through the package's get_transactions() and get_promotions().

## Fields (transactions)

get_transactions() returns 1,469,307 rows with household_id, store_id,
basket_id, product_id, quantity, sales_value, retail_disc, coupon_disc,
coupon_match_disc, week and transaction_timestamp. get_promotions() returns
product_id, store_id, display_location, mailer_location and week. These
lowercase package names map onto the raw CSVs' uppercase fields (for example
WEEK_NO to week, STORE_KEY to store_id); see dunnhumby_complete_journey.md
for the raw field map used across this project's pipeline.

## Worked example

The guide traces household 208 in campaign 18 (active 2017-10-30 to
2017-12-24), linking coupon redemptions to products and promotions to show
which item was featured (display/mailer) at purchase time, illustrating how
campaign, coupon and promotion tables join for measurement.

## Application in this project

Grounds data-dictionary explanations for how campaign, coupon and promotion
records should be interpreted, and why store/week joins are valid for
campaign measurement. It is not authority for causal claims; those belong to
Tier 1 internal records or to the methodology canon.