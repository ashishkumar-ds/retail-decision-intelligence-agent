# Verifier - checks recommendation output against concrete, checkable
# rules (not a subjective "is this a good recommendation").
from guardrails import requires_human_approval

VALID_RECOMMENDATIONS = {"CONTINUE", "MONITOR", "EXTEND_INTERVENTION", "ESCALATE", "NEEDS_REVIEW"}


def verify_recommendation(rec: dict) -> dict:
    checks = {
        "valid_recommendation_label": rec.get("recommendation") in VALID_RECOMMENDATIONS,
        "confidence_in_range": 0.0 <= rec.get("confidence", -1) <= 1.0,
        "has_reason": bool(rec.get("reason")),
        "approval_flag_matches_recommendation": rec.get("requires_human_approval") is requires_human_approval(
            rec.get("recommendation")
        ),
    }
    return {"passed": all(checks.values()), "details": checks}


def verify_batch(recommendations: list) -> dict:
    # Batch-level check: catches duplicate store_ids, which a
    # single-recommendation verifier can't see.
    store_ids = [r["store_id"] for r in recommendations]
    no_duplicates = len(store_ids) == len(set(store_ids))

    per_rec_results = [verify_recommendation(r) for r in recommendations]
    all_individually_passed = all(r["passed"] for r in per_rec_results)

    return {
        "passed": no_duplicates and all_individually_passed,
        "no_duplicate_store_ids": no_duplicates,
        "all_individually_passed": all_individually_passed,
        "failed_store_ids": [
            r["store_id"] for r, v in zip(recommendations, per_rec_results) if not v["passed"]
        ],
    }
