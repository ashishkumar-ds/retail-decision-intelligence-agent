"""Typed HTTP adapter for the shared retail forecast service with retry and exponential backoff."""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Sequence

import httpx

DEFAULT_FORECAST_API_URL = "https://retail-forecast-api-7sue.onrender.com/"
REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_RETRY_BACKOFFS = (2.0, 4.0, 8.0)
DEFAULT_WINDOW_FETCH_MAX_WORKERS = 8

logger = logging.getLogger("retail_decision_agent.forecast_tool")


class ForecastResponseError(ValueError):
    """The forecast service responded successfully with an invalid payload."""


class DataLimitation:
    """A first-class statement of what the evidence does NOT cover.

    merchant-agent pattern (`types.DataLimitation`): a figure the upstream
    service cannot supply is a typed limitation with a note - never a fake
    value, a zero, or a silent omission. This project's rule that "coverage
    gaps are evidence, never backfilled" now has a named carrier so the
    scorer, the /why narrative, and API consumers can *state* the gap.
    """

    __slots__ = ("code", "note", "detail")

    def __init__(self, code: str, note: str, detail: dict | None = None):
        self.code = code
        self.note = note
        self.detail = detail or {}

    def to_record(self) -> dict:
        return {"code": self.code, "note": self.note, "detail": dict(self.detail)}

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"DataLimitation({self.code!r})"


def actuals_limitations(envelope: dict[str, Any]) -> list[DataLimitation]:
    """Derive the coverage limitations of an actuals envelope.

    Days without observed transactions are absent from the feed by design;
    this helper surfaces those gaps (and empty/degenerate ranges) as typed
    limitations instead of letting consumers infer coverage from absence.
    """
    limitations: list[DataLimitation] = []
    start_day = envelope.get("start_day")
    end_day = envelope.get("end_day")
    observations = envelope.get("observations") or []

    if not observations:
        limitations.append(DataLimitation(
            "no_observed_sales",
            "No observed sales in the requested range; outcomes cannot be "
            "measured from actuals for this window.",
            {"start_day": start_day, "end_day": end_day},
        ))
        return limitations

    observed_days = {obs.get("day") for obs in observations if isinstance(obs, dict)}
    if isinstance(start_day, int) and isinstance(end_day, int) and end_day >= start_day:
        expected = end_day - start_day + 1
        missing = sorted(set(range(start_day, end_day + 1)) - observed_days)
        if missing:
            limitations.append(DataLimitation(
                "coverage_gap",
                "Days without observed transactions are omitted from the "
                "actuals feed; they are evidence of no coverage, not zero sales.",
                {"missing_days": missing, "observed_days": len(observed_days),
                 "expected_days": expected},
            ))
    return limitations


def _base_url() -> str:
    return os.getenv("FORECAST_API_URL", DEFAULT_FORECAST_API_URL).rstrip("/")


def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise ForecastResponseError("Forecast service returned non-JSON response") from error


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable_error(error: Exception) -> bool:
    """Determine whether an error represents a transient failure or cold start."""
    if isinstance(error, (httpx.TransportError, httpx.TimeoutException, TimeoutError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        resp = getattr(error, "response", None)
        if resp is not None and hasattr(resp, "status_code"):
            return resp.status_code in RETRYABLE_STATUS_CODES
        # Custom or mock test errors without response: check for 4xx non-429 substrings
        err_str = str(error)
        if any(f"{code}" in err_str for code in (400, 401, 403, 404, 405, 422)):
            return False
        return True
    return False


def _request_with_retry(
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> httpx.Response:
    """Execute an HTTP request with exponential backoff for network and transient HTTP errors."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
    last_error: Exception | None = None

    req_func = getattr(httpx, method.lower(), httpx.request)

    for attempt in range(retries + 1):
        try:
            if req_func is httpx.request:
                response = req_func(method, url, **kwargs)
            else:
                response = req_func(url, **kwargs)

            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError, TimeoutError) as error:
            last_error = error
            if not _is_retryable_error(error):
                logger.warning(
                    f"[FORECAST API CLIENT ERROR] non-retryable error for {method} {url}: {type(error).__name__}: {error}"
                )
                raise error
            if attempt < retries:
                backoff = backoffs[attempt] if attempt < len(backoffs) else backoffs[-1]
                logger.warning(
                    f"[FORECAST API RETRY] attempt {attempt + 1}/{retries} for {method} {url} "
                    f"failed with {type(error).__name__}: {error}. Retrying in {backoff}s..."
                )
                sleep_fn(backoff)
            else:
                logger.error(
                    f"[FORECAST API EXHAUSTED] all {retries + 1} attempts for {method} {url} "
                    f"failed. Last error: {type(error).__name__}: {error}"
                )

    if last_error:
        raise last_error
    raise httpx.HTTPError(f"Failed to execute {method} {url}")


def get_all_stores_info(
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[int, dict[str, Any]]:
    """Get metadata for all stores from /stores, indexed by integer store_id."""
    response = _request_with_retry(
        "GET",
        f"{_base_url()}/stores",
        retries=retries,
        backoffs=backoffs,
        sleep_fn=sleep_fn,
    )
    payload = _response_json(response)
    stores = payload.get("stores") if isinstance(payload, dict) and "stores" in payload else payload
    if not isinstance(stores, list):
        raise ForecastResponseError("/stores payload must be a list or contain a 'stores' list")
    stores_by_id: dict[int, dict[str, Any]] = {}
    for store in stores:
        if not isinstance(store, dict):
            raise ForecastResponseError("/stores payload contains a non-object store")
        candidate_id = store.get("store_id", store.get("id"))
        if isinstance(candidate_id, bool) or not isinstance(candidate_id, int):
            continue
        if not isinstance(store.get("last_day"), int) or isinstance(store.get("last_day"), bool):
            raise ForecastResponseError("Store metadata is missing integer 'last_day'")
        stores_by_id[candidate_id] = store
    return stores_by_id


def get_store_info(
    store_id: int,
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """Get a store's metadata, or ``None`` when the service has no such store.

    HTTP and network errors deliberately propagate as ``httpx`` exceptions after retries.
    """
    stores_by_id = get_all_stores_info(retries=retries, backoffs=backoffs, sleep_fn=sleep_fn)
    return stores_by_id.get(store_id)


def warm_up(max_wait_seconds: float = 120.0, sleep_fn: Callable[[float], None] | None = None) -> bool:
    """Ping /health until the service answers (or the budget is spent).

    Render free-tier cold starts take 30-60s; callers should warm the
    service once BEFORE fanning out many requests, so a cold start is
    absorbed by one poll loop instead of failing every store's first call.
    Returns True when the service responded.
    """
    # Resolve the delay at call time (not a def-time default) so tests can
    # patch forecast_tool.time.sleep without hitting a stale bound default.
    delay = sleep_fn if sleep_fn is not None else time.sleep
    url = f"{_base_url()}/health"
    deadline = time.monotonic() + max_wait_seconds
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            response = httpx.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            if response.status_code < 500:
                logger.info("[FORECAST WARM-UP] service answering after %d attempt(s)", attempt)
                return True
            logger.info("[FORECAST WARM-UP] attempt %d: HTTP %s (cold start?)", attempt, response.status_code)
        except httpx.HTTPError as error:
            logger.info("[FORECAST WARM-UP] attempt %d failed: %s", attempt, type(error).__name__)
        delay(5.0)
    logger.warning("[FORECAST WARM-UP] service not answering within %.0fs", max_wait_seconds)
    return False


def get_prediction(
    store_id: int,
    day: int,
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> float:
    """Request a numerical forecast prediction for a store and day with automatic retry."""
    response = _request_with_retry(
        "POST",
        f"{_base_url()}/predict",
        json={"store_id": store_id, "day": day},
        retries=retries,
        backoffs=backoffs,
        sleep_fn=sleep_fn,
    )
    payload = _response_json(response)
    if not isinstance(payload, dict):
        raise ForecastResponseError("/predict payload must be an object")
    prediction = payload.get("predicted_sales_value")
    if isinstance(prediction, bool) or not isinstance(prediction, (int, float)):
        raise ForecastResponseError("/predict payload is missing numeric 'predicted_sales_value'")
    return float(prediction)


def get_evaluation_window_forecast(
    store_id: int,
    start_day: int,
    window_start_offset: int = 47,
    window_end_offset: int = 60,
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_workers: int = DEFAULT_WINDOW_FETCH_MAX_WORKERS,
) -> float:
    """Fetch daily forecasts for Day +47 through Day +60 and return the arithmetic mean.

    Parameters:
        store_id: Store ID to evaluate.
        start_day: Base day index corresponding to intervention start.
        window_start_offset: Start day offset relative to start_day (default 47 for Day +47).
        window_end_offset: End day offset relative to start_day (default 60 for Day +60).
        retries: Number of retries per daily request.
        backoffs: Backoff seconds tuple (default 2s, 4s, 8s).
        sleep_fn: Sleep function (injectable for testing).
        max_workers: Days are fetched concurrently, bounded by this pool size,
            so that per-day retry/backoff delays don't compound sequentially
            across the whole window (up to ~74s worst case per day otherwise).

    Returns:
        float: Arithmetic mean of the 14 daily sales predictions. Day order
        of the underlying HTTP calls is not guaranteed - only the resulting
        mean is deterministic.

    Raises:
        TypeError: If store_id, start_day, or offsets are not integers (or are bool).
        ValueError: If window_start_offset > window_end_offset.
        ForecastResponseError: If any daily prediction is missing, malformed, or incomplete.
        httpx.HTTPError: If network or HTTP errors persist after all retries.
    """
    if isinstance(store_id, bool) or not isinstance(store_id, int):
        raise TypeError("store_id must be an integer")
    if isinstance(start_day, bool) or not isinstance(start_day, int):
        raise TypeError("start_day must be an integer")
    if isinstance(window_start_offset, bool) or not isinstance(window_start_offset, int):
        raise TypeError("window_start_offset must be an integer")
    if isinstance(window_end_offset, bool) or not isinstance(window_end_offset, int):
        raise TypeError("window_end_offset must be an integer")
    if window_start_offset > window_end_offset:
        raise ValueError("window_start_offset must not exceed window_end_offset")

    target_days = [start_day + offset for offset in range(window_start_offset, window_end_offset + 1)]

    def fetch_one(target_day: int) -> float:
        pred = get_prediction(store_id, target_day, retries=retries, backoffs=backoffs, sleep_fn=sleep_fn)
        if pred is None or isinstance(pred, bool) or not isinstance(pred, (int, float)):
            raise ForecastResponseError(f"Incomplete forecast: received invalid prediction for day {target_day}")
        return float(pred)

    daily_predictions: list[float] = []
    executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(target_days))))
    try:
        futures = {executor.submit(fetch_one, day): day for day in target_days}
        for future in as_completed(futures):
            daily_predictions.append(future.result())
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    if not daily_predictions:
        raise ValueError("No daily predictions collected for evaluation window")

    return sum(daily_predictions) / len(daily_predictions)

def get_control_comparison(
    store_id: int,
    pre_start: int,
    pre_end: int,
    post_start: int,
    post_end: int,
    *,
    k: int = 10,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch a matched-control (DiD) comparison for a store from the forecast service.

    Returns the service envelope: ``{"store_id", "windows", "matched_controls",
    "causal": {"did_uplift_pct", ...}, "methodology"}``. The causal field's
    ``did_uplift_pct`` - treated vs matched-control change - is the scale-up
    decision input; raw own-baseline lift is NOT causal (market drift, model bias).

    Raises:
        TypeError: If arguments are not integers (or are bool).
        ValueError: If the windows are not ordered correctly.
        ForecastResponseError: If the payload is malformed.
        httpx.HTTPError: If network or HTTP errors persist after retries.
    """
    for name, value in (("store_id", store_id), ("pre_start", pre_start), ("pre_end", pre_end),
                        ("post_start", post_start), ("post_end", post_end), ("k", k)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
    if not (pre_start < pre_end < post_start <= post_end):
        raise ValueError("windows must satisfy pre_start < pre_end < post_start <= post_end")

    url = f"{_base_url()}/controls/{store_id}"
    response = _request_with_retry(
        "GET", url,
        params={"pre_start": pre_start, "pre_end": pre_end,
                "post_start": post_start, "post_end": post_end, "k": k},
        retries=retries, backoffs=backoffs, sleep_fn=sleep_fn,
    )
    payload = _response_json(response)

    if not isinstance(payload, dict):
        raise ForecastResponseError("Controls service response must be an object")
    required = {"store_id", "windows", "matched_controls", "causal", "methodology"}
    missing = required - set(payload)
    if missing:
        raise ForecastResponseError(f"Controls response missing fields: {sorted(missing)}")
    if payload.get("store_id") != store_id:
        raise ForecastResponseError(f"Controls response store_id mismatch: {payload.get('store_id')} != {store_id}")
    causal = payload.get("causal")
    if not isinstance(causal, dict) or "did_uplift_pct" not in causal:
        raise ForecastResponseError("Controls response 'causal' must include did_uplift_pct")
    if causal.get("did_uplift_pct") is not None and (
        not isinstance(causal["did_uplift_pct"], (int, float)) or isinstance(causal["did_uplift_pct"], bool)
    ):
        raise ForecastResponseError("Controls response did_uplift_pct must be numeric or null")
    controls = payload.get("matched_controls")
    if not isinstance(controls, list) or not controls:
        raise ForecastResponseError("Controls response matched_controls must be a non-empty list")
    return payload


def get_actuals(
    store_id: int,
    start_day: int,
    end_day: int,
    *,
    retries: int = 3,
    backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch observed daily sales for a store from the forecast service.

    Returns the service envelope: ``{"store_id", "start_day", "end_day",
    "range_start_date", "range_end_date", "observation_count", "observations"}``
    where each observation is ``{"day", "date", "sales_value"}``.

    Days without observed transactions are absent from the response - coverage
    gaps are genuine evidence states, never backfilled or invented.

    Raises:
        TypeError: If arguments are not integers (or are bool).
        ValueError: If start_day exceeds end_day.
        ForecastResponseError: If the payload is malformed.
        httpx.HTTPError: If network or HTTP errors persist after retries.
    """
    if isinstance(store_id, bool) or not isinstance(store_id, int):
        raise TypeError("store_id must be an integer")
    if isinstance(start_day, bool) or not isinstance(start_day, int):
        raise TypeError("start_day must be an integer")
    if isinstance(end_day, bool) or not isinstance(end_day, int):
        raise TypeError("end_day must be an integer")
    if start_day > end_day:
        raise ValueError("start_day must not exceed end_day")

    url = f"{_base_url()}/actuals/{store_id}"
    response = _request_with_retry("GET", url, params={"start_day": start_day, "end_day": end_day},
                                   retries=retries, backoffs=backoffs, sleep_fn=sleep_fn)
    payload = _response_json(response)

    if not isinstance(payload, dict):
        raise ForecastResponseError("Actuals service response must be an object")
    required = {"store_id", "start_day", "end_day", "range_start_date", "range_end_date", "observations"}
    missing = required - set(payload)
    if missing:
        raise ForecastResponseError(f"Actuals service response missing fields: {sorted(missing)}")
    if payload.get("store_id") != store_id:
        raise ForecastResponseError(f"Actuals response store_id mismatch: {payload.get('store_id')} != {store_id}")
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ForecastResponseError("Actuals service response observations must be a list")
    for index, obs in enumerate(observations):
        if not isinstance(obs, dict) or not {"day", "date", "sales_value"} <= set(obs):
            raise ForecastResponseError(f"Actuals observation {index} is malformed")
        if not isinstance(obs["sales_value"], (int, float)) or isinstance(obs["sales_value"], bool):
            raise ForecastResponseError(f"Actuals observation {index} sales_value must be numeric")
    return payload


