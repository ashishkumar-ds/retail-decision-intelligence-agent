# Planner - turns a route into step keys that app/main.py executes
# directly (not just a prose description). This is a lookup table.

# "flag_for_review" short-circuits and skips scoring, since "no_data"
# already established there's nothing to score.
PLANS = {
    "no_data": ["flag_for_review"],
    "near_deadline": ["score_and_recommend"],
    "standard": ["score_and_recommend"],
}

# Descriptions used only for logging/display - kept separate from PLANS
# so the executable plan and its description can't drift apart.
STEP_DESCRIPTIONS = {
    "flag_for_review": "no forecast signal - skip scoring, flag for mandatory analyst review",
    "score_and_recommend": "compute recovery_pct, velocity, health_score; apply decision rules",
}


def build_plan(route: str) -> list:
    return PLANS.get(route, ["flag_for_review"])


def describe_plan(plan: list) -> list:
    return [STEP_DESCRIPTIONS.get(step, step) for step in plan]
