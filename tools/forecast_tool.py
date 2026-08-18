"""Typed HTTP adapter for the shared retail forecast service with retry and exponential backoff."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Sequence

import requests

DEFAULT_FORECAST_API_URL = "https://retail-forecast-api-7sue.onrender.com/"
REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_RETRY_BACKOFFS = (2.0, 4.0, 8.0)

logger = logging.getLogger("retail_decision_agent.forecast_tool")


class ForecastResponseError(ValueError):
    """The forecast service responded successfully with an invalid payload."""


def _base_url() -> str:
    return os.getenv("FORECAST_API_URL", DEFAULT_FORECAST_API_URL).rstrip("/")


def _response_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise ForecastResponseError("Forecast service returned non-JSON response") from error


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable_error(error: Exception) -> bool:
    """Determine whether an error represents a transient failure or cold start."""
    if isinstance(error, (requests.ConnectionError, requests.Timeout, TimeoutError)):
        return True
    if isinstance(error, requests.HTTPError):
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
) -> requests.Response:
    """Execute an HTTP request with exponential backoff for network and transient HTTP errors."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
    last_error: Exception | None = None

    req_func = getattr(requests, method.lower(), requests.request)

    for attempt in range(retries + 1):
        try:
            if req_func is requests.request:
                response = req_func(method, url, **kwargs)
            else:
                response = req_func(url, **kwargs)

            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError, TimeoutError) as error:
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
    raise requests.RequestException(f"Failed to execute {method} {url}")


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

    HTTP and network errors deliberately propagate as ``requests`` exceptions after retries.
    """
    stores_by_id = get_all_stores_info(retries=retries, backoffs=backoffs, sleep_fn=sleep_fn)
    return stores_by_id.get(store_id)


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

    Returns:
        float: Arithmetic mean of the 14 daily sales predictions.

    Raises:
        TypeError: If store_id, start_day, or offsets are not integers (or are bool).
        ValueError: If window_start_offset > window_end_offset.
        ForecastResponseError: If any daily prediction is missing, malformed, or incomplete.
        requests.RequestException: If network or HTTP errors persist after all retries.
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

    daily_predictions: list[float] = []
    for day_offset in range(window_start_offset, window_end_offset + 1):
        target_day = start_day + day_offset
        pred = get_prediction(
            store_id,
            target_day,
            retries=retries,
            backoffs=backoffs,
            sleep_fn=sleep_fn,
        )
        if pred is None or isinstance(pred, bool) or not isinstance(pred, (int, float)):
            raise ForecastResponseError(f"Incomplete forecast: received invalid prediction for day {target_day}")
        daily_predictions.append(float(pred))

    if not daily_predictions:
        raise ValueError("No daily predictions collected for evaluation window")

    return sum(daily_predictions) / len(daily_predictions)
