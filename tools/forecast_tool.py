"""Typed HTTP adapter for the shared retail forecast service."""
import os
from typing import Any

import requests

DEFAULT_FORECAST_API_URL = "https://retail-forecast-api-7sue.onrender.com/"
REQUEST_TIMEOUT_SECONDS = 10


class ForecastResponseError(ValueError):
    """The forecast service responded successfully with an invalid payload."""


def _base_url() -> str:
    return os.getenv("FORECAST_API_URL", DEFAULT_FORECAST_API_URL).rstrip("/")


def _response_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise ForecastResponseError("Forecast service returned non-JSON response") from error


def get_store_info(store_id: int) -> dict[str, Any] | None:
    """Get a store's metadata, or ``None`` when the service has no such store.

    HTTP and network errors deliberately propagate as ``requests`` exceptions.
    """
    response = requests.get(f"{_base_url()}/stores", timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = _response_json(response)
    stores = payload.get("stores") if isinstance(payload, dict) and "stores" in payload else payload
    if not isinstance(stores, list):
        raise ForecastResponseError("/stores payload must be a list or contain a 'stores' list")
    for store in stores:
        if not isinstance(store, dict):
            raise ForecastResponseError("/stores payload contains a non-object store")
        candidate_id = store.get("store_id", store.get("id"))
        if candidate_id == store_id:
            if not isinstance(store.get("last_day"), int):
                raise ForecastResponseError("Store metadata is missing integer 'last_day'")
            return store
    return None


def get_prediction(store_id: int, day: int) -> float:
    """Request a numerical forecast prediction for a store and day."""
    response = requests.post(
        f"{_base_url()}/predict",
        json={"store_id": store_id, "day": day},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = _response_json(response)
    if not isinstance(payload, dict):
        raise ForecastResponseError("/predict payload must be an object")
    prediction = payload.get("predicted_sales_value")
    if isinstance(prediction, bool) or not isinstance(prediction, (int, float)):
        raise ForecastResponseError("/predict payload is missing numeric 'predicted_sales_value'")
    return float(prediction)
