# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyCustomFieldsClient (T005-T007)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import BASE_URL, TOKEN_URL
from custom_components.guesty.api.custom_fields import (
    GuestyCustomFieldsClient,
)
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyCustomFieldError,
)
from tests.conftest import (
    FakeTokenStorage,
    make_token_response,
)

# Reusable API response building blocks

_FIELD_DEF_DOOR_CODE: dict[str, Any] = {
    "id": "637bad36abcdef123456",
    "name": "Door Code",
    "type": "string",
    "objectType": "reservation",
}

_FIELD_DEF_ALERT: dict[str, Any] = {
    "id": "637bad36abcdef789012",
    "name": "Maintenance Alert",
    "type": "boolean",
    "objectType": "listing",
}


def _make_custom_fields_client() -> GuestyCustomFieldsClient:
    """Create a GuestyCustomFieldsClient with test defaults.

    Returns:
        A GuestyCustomFieldsClient backed by test fakes.
    """
    storage = FakeTokenStorage()
    http = httpx.AsyncClient()
    token_manager = GuestyTokenManager(
        client_id="test-client-id",
        client_secret="test-client-secret",
        http_client=http,
        storage=storage,
        refresh_buffer=0,
    )
    api_client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
    )
    return GuestyCustomFieldsClient(api_client)


class TestGetDefinitions:
    """Tests for GuestyCustomFieldsClient.get_definitions (T005)."""

    @respx.mock
    async def test_successful_fetch_returns_definitions(
        self,
    ) -> None:
        """Successful fetch returns list of definitions."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/custom-fields").mock(
            return_value=Response(
                200,
                json=[_FIELD_DEF_DOOR_CODE, _FIELD_DEF_ALERT],
            ),
        )
        client = _make_custom_fields_client()
        definitions = await client.get_definitions()
        assert len(definitions) == 2
        assert definitions[0].field_id == "637bad36abcdef123456"
        assert definitions[0].name == "Door Code"
        assert definitions[0].field_type == "text"
        assert definitions[1].field_id == "637bad36abcdef789012"
        assert definitions[1].field_type == "boolean"

    @respx.mock
    async def test_empty_array_returns_empty_list(self) -> None:
        """Empty array from API returns empty list."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/custom-fields").mock(
            return_value=Response(200, json=[]),
        )
        client = _make_custom_fields_client()
        definitions = await client.get_definitions()
        assert definitions == []

    @respx.mock
    async def test_missing_required_fields_skipped(self) -> None:
        """Definitions with missing required fields are skipped."""
        incomplete = {"id": "abc", "name": "Incomplete"}
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/custom-fields").mock(
            return_value=Response(
                200,
                json=[_FIELD_DEF_DOOR_CODE, incomplete],
            ),
        )
        client = _make_custom_fields_client()
        definitions = await client.get_definitions()
        assert len(definitions) == 1
        assert definitions[0].field_id == "637bad36abcdef123456"

    @respx.mock
    async def test_api_error_propagation(self) -> None:
        """API errors propagate from get_definitions."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/custom-fields").mock(
            return_value=Response(403, json={"error": "forbidden"}),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyAuthError):
            await client.get_definitions()

    @respx.mock
    async def test_non_success_status_raises_error(self) -> None:
        """Non-2xx status raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/custom-fields").mock(
            return_value=Response(
                422,
                json={"error": "Unprocessable"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="422"):
            await client.get_definitions()

    @respx.mock
    async def test_non_list_response_raises_error(self) -> None:
        """Non-list response from API raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/custom-fields").mock(
            return_value=Response(
                200,
                json={"results": []},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            await client.get_definitions()

    @respx.mock
    async def test_invalid_json_response_raises_error(
        self,
    ) -> None:
        """Non-JSON response from API raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/custom-fields").mock(
            return_value=Response(
                200,
                content=b"not json",
                headers={"content-type": "text/plain"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="JSON"):
            await client.get_definitions()


class TestSetField:
    """Tests for GuestyCustomFieldsClient.set_field (T006)."""

    @respx.mock
    async def test_listing_target_builds_correct_put(
        self,
    ) -> None:
        """Listing target sends PUT to listings custom-fields path."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listing_route = respx.put(
            f"{BASE_URL}/listings/lst-123/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json=[
                    {
                        "fieldId": "cf-abc",
                        "value": "new-code",
                    }
                ],
            ),
        )
        client = _make_custom_fields_client()
        result = await client.set_field(
            target_type="listing",
            target_id="lst-123",
            field_id="cf-abc",
            value="new-code",
        )
        assert result.success is True
        assert result.target_type == "listing"
        assert result.target_id == "lst-123"
        assert result.field_id == "cf-abc"
        assert listing_route.called
        request = listing_route.calls.last.request
        import json as _json

        assert _json.loads(request.content) == [
            {"fieldId": "cf-abc", "value": "new-code"},
        ]

    @respx.mock
    async def test_reservation_target_builds_v3_put(
        self,
    ) -> None:
        """Reservation target sends PUT to reservations-v3 path."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        res_route = respx.put(
            f"{BASE_URL}/reservations-v3/res-456/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json={
                    "reservationId": "res-456",
                    "customFields": [
                        {
                            "_id": "68f9fa36abc",
                            "fieldId": "cf-def",
                            "value": 42,
                        }
                    ],
                },
            ),
        )
        client = _make_custom_fields_client()
        result = await client.set_field(
            target_type="reservation",
            target_id="res-456",
            field_id="cf-def",
            value=42,
        )
        assert result.success is True
        assert result.target_type == "reservation"
        assert result.target_id == "res-456"
        assert res_route.called

    @respx.mock
    async def test_400_raises_custom_field_error(self) -> None:
        """400 Bad Request raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-999/custom-fields",
        ).mock(
            return_value=Response(
                400,
                json={"error": "Invalid field ID"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError) as exc_info:
            await client.set_field(
                target_type="listing",
                target_id="lst-999",
                field_id="cf-bad",
                value="x",
            )
        assert exc_info.value.target_type == "listing"
        assert exc_info.value.target_id == "lst-999"
        assert exc_info.value.field_id == "cf-bad"

    @respx.mock
    async def test_404_raises_custom_field_error(self) -> None:
        """404 Not Found raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-gone/custom-fields",
        ).mock(
            return_value=Response(
                404,
                json={"error": "Not found"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError) as exc_info:
            await client.set_field(
                target_type="listing",
                target_id="lst-gone",
                field_id="cf-missing",
                value="y",
            )
        assert exc_info.value.target_type == "listing"
        assert exc_info.value.target_id == "lst-gone"

    @respx.mock
    async def test_422_raises_custom_field_error(self) -> None:
        """422 Unprocessable Entity raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-err/custom-fields",
        ).mock(
            return_value=Response(
                422,
                json={"error": "Validation failed"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            await client.set_field(
                target_type="listing",
                target_id="lst-err",
                field_id="cf-val",
                value="z",
            )

    @respx.mock
    async def test_error_with_non_json_body(self) -> None:
        """Error response with non-JSON body uses text fallback."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-txt/custom-fields",
        ).mock(
            return_value=Response(
                400,
                content=b"Bad Request",
                headers={"content-type": "text/plain"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="400"):
            await client.set_field(
                target_type="listing",
                target_id="lst-txt",
                field_id="cf-txt",
                value="v",
            )

    @respx.mock
    async def test_error_body_truncated(self) -> None:
        """Long error response body is truncated in exception."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        long_body = {"error": "x" * 300}
        respx.put(
            f"{BASE_URL}/listings/lst-long/custom-fields",
        ).mock(
            return_value=Response(400, json=long_body),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError) as exc_info:
            await client.set_field(
                target_type="listing",
                target_id="lst-long",
                field_id="cf-long",
                value="v",
            )
        assert "..." in exc_info.value.message

    @respx.mock
    async def test_api_error_propagation(self) -> None:
        """API auth errors propagate from set_field."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-auth/custom-fields",
        ).mock(
            return_value=Response(
                403,
                json={"error": "Forbidden"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyAuthError):
            await client.set_field(
                target_type="listing",
                target_id="lst-auth",
                field_id="cf-auth",
                value="v",
            )

    async def test_invalid_target_type_raises_error(
        self,
    ) -> None:
        """Invalid target_type raises GuestyCustomFieldError."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="target"):
            await client.set_field(
                target_type="unknown",
                target_id="x",
                field_id="y",
                value="z",
            )

    async def test_empty_target_id_raises_error(self) -> None:
        """Empty target_id raises GuestyCustomFieldError."""
        client = _make_custom_fields_client()
        with pytest.raises(
            GuestyCustomFieldError,
            match="target_id",
        ):
            await client.set_field(
                target_type="listing",
                target_id="",
                field_id="cf-1",
                value="v",
            )

    async def test_empty_field_id_raises_error(self) -> None:
        """Empty field_id raises GuestyCustomFieldError."""
        client = _make_custom_fields_client()
        with pytest.raises(
            GuestyCustomFieldError,
            match="field_id",
        ):
            await client.set_field(
                target_type="listing",
                target_id="lst-1",
                field_id="",
                value="v",
            )

    @respx.mock
    async def test_unexpected_non_2xx_raises_error(self) -> None:
        """Unexpected non-2xx status raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.put(
            f"{BASE_URL}/listings/lst-conflict/custom-fields",
        ).mock(
            return_value=Response(
                409,
                json={"error": "Conflict"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="409"):
            await client.set_field(
                target_type="listing",
                target_id="lst-conflict",
                field_id="cf-c",
                value="v",
            )


class TestValidateValue:
    """Tests for GuestyCustomFieldsClient.validate_value (T007)."""

    def test_text_accepts_string(self) -> None:
        """Text field type accepts string value."""
        client = _make_custom_fields_client()
        client.validate_value("hello", "text")

    def test_text_rejects_int(self) -> None:
        """Text field type rejects int value."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value(42, "text")

    def test_text_rejects_float(self) -> None:
        """Text field type rejects float value."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value(3.14, "text")

    def test_text_rejects_bool(self) -> None:
        """Text field type rejects bool value."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value(True, "text")

    def test_number_accepts_int(self) -> None:
        """Number field type accepts int value."""
        client = _make_custom_fields_client()
        client.validate_value(42, "number")

    def test_number_accepts_float(self) -> None:
        """Number field type accepts float value."""
        client = _make_custom_fields_client()
        client.validate_value(3.14, "number")

    def test_number_rejects_string(self) -> None:
        """Number field type rejects string value."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value("hello", "number")

    def test_number_rejects_bool(self) -> None:
        """Number field type rejects bool (bool subclasses int)."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value(True, "number")

    def test_boolean_accepts_bool(self) -> None:
        """Boolean field type accepts bool value."""
        client = _make_custom_fields_client()
        client.validate_value(True, "boolean")

    def test_boolean_rejects_string(self) -> None:
        """Boolean field type rejects string value."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value("true", "boolean")

    def test_boolean_rejects_int(self) -> None:
        """Boolean field type rejects int value."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value(1, "boolean")

    def test_boolean_rejects_float(self) -> None:
        """Boolean field type rejects float value."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError):
            client.validate_value(1.0, "boolean")

    def test_unknown_type_skips_validation(self) -> None:
        """Unknown field type does not raise an error."""
        client = _make_custom_fields_client()
        client.validate_value("anything", "multiline")
        client.validate_value(42, "multiline")

    def test_type_mismatch_error_message(self) -> None:
        """Type mismatch includes helpful error message."""
        client = _make_custom_fields_client()
        with pytest.raises(
            GuestyCustomFieldError,
            match="text",
        ):
            client.validate_value(42, "text")
