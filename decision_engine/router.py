# Router - decides which evaluation path a store needs before scoring.
# Deliberately a lookup, not a model call.
from decision_engine.scorer import StoreSignal
 
 
def route(signal: StoreSignal) -> str:
    """
    Returns one of:
      - "no_data"        - forecast signal unavailable, cannot evaluate
      - "near_deadline"   - fewer than 14 days remain in the recovery window
      - "standard"        - normal evaluation path
    """
    if not signal.forecast_signal_available:
        return "no_data"
    if signal.days_remaining < 14:
        return "near_deadline"
    return "standard"
