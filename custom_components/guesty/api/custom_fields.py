# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Client for Guesty custom field operations."""

from __future__ import annotations

import json
from typing import Any

from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    CUSTOM_FIELD_TARGETS,
    CUSTOM_FIELDS_ENDPOINT,
    LISTING_CUSTOM_FIELDS_PATH,
    RESERVATION_CUSTOM_FIELDS_PATH,
)
from custom_components.guesty.api.exceptions import (
    GuestyCustomFieldError,
)
from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyCustomFieldResult,
)

# Validators keyed by field_type → allowed Python types.
# bool is excluded from number because ``bool`` subclasses ``int``.
_TYPE_VALIDATORS: dict[str, tuple[type, ...]] = {
    "text": (str,),
    "number": (int, float),
    "boolean": (bool,),
}


class GuestyCustomFieldsClient:
    """Client for Guesty custom field operations.

    Provides definition fetching, value writing, and input
    validation with zero Home Assistant imports.

    Attributes:
        _api_client: Guesty API client for HTTP communication.
        _account_id: Guesty account ID for endpoint resolution.
    """

    def __init__(
        self,
        api_client: GuestyApiClient,
        *,
        account_id: str | None = None,
    ) -> None:
        """Initialize GuestyCustomFieldsClient.

        Args:
            api_client: Guesty API client for HTTP requests.
            account_id: Guesty account ID for the custom
                fields definitions endpoint. Optional; when
                None, ``get_definitions()`` will raise.
        """
        self._api_client = api_client
        self._account_id = account_id

    async def get_definitions(
        self,
    ) -> list[GuestyCustomFieldDefinition]:
        """Fetch all custom field definitions from the API.

        Returns:
            List of parsed custom field definitions. Entries
            with missing required fields are silently filtered.

        Raises:
            GuestyCustomFieldError: On non-2xx HTTP status,
                unexpected response format, or missing
                account_id.
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
        """
        if not self._account_id or not self._account_id.strip():
            raise GuestyCustomFieldError(
                "Cannot fetch custom field definitions without an account ID",
            )

        account_id = self._account_id.strip()
        response = await self._api_client._request(
            "GET",
            CUSTOM_FIELDS_ENDPOINT.format(
                account_id=account_id,
            ),
        )

        if not response.is_success:
            raise GuestyCustomFieldError(
                "Failed to fetch custom field definitions: "
                f"HTTP {response.status_code}",
            )

        try:
            data = response.json()
        except Exception as exc:
            raise GuestyCustomFieldError(
                "Custom fields response is not valid JSON",
            ) from exc

        if not isinstance(data, list):
            raise GuestyCustomFieldError(
                "Custom fields response must be a JSON array",
            )

        return [
            definition
            for item in data
            if isinstance(item, dict)
            and (
                definition := GuestyCustomFieldDefinition.from_api_dict(
                    item,
                )
            )
            is not None
        ]

    async def get_reservation_fields(
        self,
        reservation_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch custom field values for a reservation.

        Args:
            reservation_id: The Guesty reservation identifier.

        Returns:
            List of custom field value dicts, each containing
            ``fieldId`` and ``value`` keys.

        Raises:
            GuestyCustomFieldError: On non-2xx HTTP status or
                unexpected response format.
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
        """
        if not reservation_id or not isinstance(reservation_id, str):
            raise GuestyCustomFieldError(
                "reservation_id must be a non-empty string",
            )

        path = RESERVATION_CUSTOM_FIELDS_PATH.format(
            reservation_id=reservation_id.strip(),
        )

        response = await self._api_client._request("GET", path)

        if not response.is_success:
            raise GuestyCustomFieldError(
                "Failed to fetch reservation custom fields: "
                f"HTTP {response.status_code}",
            )

        try:
            data: dict[str, Any] = response.json()
        except Exception as exc:
            raise GuestyCustomFieldError(
                "Reservation custom fields response is not valid JSON",
            ) from exc

        if not isinstance(data, dict):
            raise GuestyCustomFieldError(
                "Reservation custom fields response must be a JSON object",
            )

        custom_fields = data.get("customFields", [])
        if not isinstance(custom_fields, list):
            raise GuestyCustomFieldError(
                "customFields must be a JSON array",
            )

        return [
            {"fieldId": item["fieldId"], "value": item.get("value", "")}
            for item in custom_fields
            if isinstance(item, dict) and "fieldId" in item
        ]

    async def set_field(
        self,
        target_type: str,
        target_id: str,
        field_id: str,
        value: str | int | float | bool,
    ) -> GuestyCustomFieldResult:
        """Write a custom field value to a listing or reservation.

        Args:
            target_type: Entity type ('listing' or 'reservation').
            target_id: Entity identifier.
            field_id: Custom field identifier.
            value: New value for the custom field.

        Returns:
            GuestyCustomFieldResult indicating success or failure.

        Raises:
            GuestyCustomFieldError: On invalid target type or any
                non-2xx HTTP response.
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
        """
        if target_type not in CUSTOM_FIELD_TARGETS:
            raise GuestyCustomFieldError(
                f"Invalid target type '{target_type}'; "
                f"expected one of {sorted(CUSTOM_FIELD_TARGETS)}",
                target_type=target_type,
                target_id=target_id,
                field_id=field_id,
            )

        if not target_id or not isinstance(target_id, str):
            raise GuestyCustomFieldError(
                "target_id must be a non-empty string",
                target_type=target_type,
                target_id=target_id,
                field_id=field_id,
            )

        if not field_id or not isinstance(field_id, str):
            raise GuestyCustomFieldError(
                "field_id must be a non-empty string",
                target_type=target_type,
                target_id=target_id,
                field_id=field_id,
            )

        if target_type == "listing":
            path = LISTING_CUSTOM_FIELDS_PATH.format(
                listing_id=target_id,
            )
        else:
            path = RESERVATION_CUSTOM_FIELDS_PATH.format(
                reservation_id=target_id,
            )

        body: dict[str, object] = {
            "customFields": [{"fieldId": field_id, "value": value}],
        }

        response = await self._api_client._request(
            "PUT",
            path,
            json_data=body,
        )

        if response.status_code in (400, 404, 422):
            try:
                detail = json.dumps(response.json())
            except Exception:
                detail = response.text
            # Truncate to avoid leaking sensitive values.
            if len(detail) > 200:
                detail = detail[:200] + "..."
            raise GuestyCustomFieldError(
                f"Custom field update failed ({response.status_code}): {detail}",
                target_type=target_type,
                target_id=target_id,
                field_id=field_id,
            )

        if not response.is_success:
            raise GuestyCustomFieldError(
                f"Custom field update failed: HTTP {response.status_code}",
                target_type=target_type,
                target_id=target_id,
                field_id=field_id,
            )

        return GuestyCustomFieldResult(
            success=True,
            target_type=target_type,
            target_id=target_id,
            field_id=field_id,
        )

    def validate_value(
        self,
        value: str | int | float | bool,
        field_type: str,
    ) -> None:
        """Validate a value against a custom field type.

        Bool is checked before numeric types because ``bool``
        subclasses ``int`` in Python.

        Args:
            value: The value to validate.
            field_type: The expected field type.

        Raises:
            GuestyCustomFieldError: On type mismatch.
        """
        allowed = _TYPE_VALIDATORS.get(field_type)
        if allowed is None:
            return

        # Reject bool early for number fields.
        if field_type == "number" and isinstance(value, bool):
            raise GuestyCustomFieldError(
                f"Expected numeric value for field type '{field_type}', got bool",
            )

        if not isinstance(value, allowed):
            raise GuestyCustomFieldError(
                f"Expected {'/'.join(t.__name__ for t in allowed)} "
                f"for field type '{field_type}', "
                f"got {type(value).__name__}",
            )
