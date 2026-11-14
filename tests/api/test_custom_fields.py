# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyCustomFieldsClient (T005-T007)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    RESERVATION_BY_ID_LEGACY_PATH,
    RESERVATION_CUSTOM_FIELDS_PATH,
    RESERVATIONS_API_V3,
    TOKEN_URL,
    WRITE_ROUTE_LEGACY,
)
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

_FAKE_ACCOUNT_ID: str = "acc-test-123"

_FIELD_DEF_DOOR_CODE: dict[str, Any] = {
    "fieldId": "637bad36abcdef123456",
    "key": "Door Code",
    "type": "text",
    "object": "reservation",
    "displayName": "door_code",
    "isPublic": False,
    "options": [],
}

_FIELD_DEF_ALERT: dict[str, Any] = {
    "fieldId": "637bad36abcdef789012",
    "key": "Maintenance Alert",
    "type": "boolean",
    "object": "listing",
    "displayName": "maintenance_alert",
    "isPublic": True,
    "isRequired": True,
    "options": [],
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
    return GuestyCustomFieldsClient(
        api_client,
        account_id=_FAKE_ACCOUNT_ID,
    )


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
        respx.get(f"{BASE_URL}/accounts/{_FAKE_ACCOUNT_ID}/custom-fields").mock(
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
        respx.get(f"{BASE_URL}/accounts/{_FAKE_ACCOUNT_ID}/custom-fields").mock(
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
        respx.get(f"{BASE_URL}/accounts/{_FAKE_ACCOUNT_ID}/custom-fields").mock(
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
        respx.get(f"{BASE_URL}/accounts/{_FAKE_ACCOUNT_ID}/custom-fields").mock(
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
        respx.get(f"{BASE_URL}/accounts/{_FAKE_ACCOUNT_ID}/custom-fields").mock(
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
        respx.get(f"{BASE_URL}/accounts/{_FAKE_ACCOUNT_ID}/custom-fields").mock(
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
        respx.get(f"{BASE_URL}/accounts/{_FAKE_ACCOUNT_ID}/custom-fields").mock(
            return_value=Response(
                200,
                content=b"not json",
                headers={"content-type": "text/plain"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="JSON"):
            await client.get_definitions()

    async def test_no_account_id_raises_error(self) -> None:
        """get_definitions raises when account_id is None."""
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
        client = GuestyCustomFieldsClient(api_client)
        with pytest.raises(
            GuestyCustomFieldError,
            match="account ID",
        ):
            await client.get_definitions()

    async def test_empty_account_id_raises_error(self) -> None:
        """get_definitions raises when account_id is empty."""
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
        client = GuestyCustomFieldsClient(
            api_client,
            account_id="   ",
        )
        with pytest.raises(
            GuestyCustomFieldError,
            match="account ID",
        ):
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

        assert _json.loads(request.content) == {
            "customFields": [{"fieldId": "cf-abc", "value": "new-code"}],
        }

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


class TestGetFieldValues:
    """Tests for GuestyCustomFieldsClient.get_field_values."""

    @respx.mock
    async def test_listing_returns_bare_array(self) -> None:
        """Listing target returns normalised list from bare array."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/listings/lst-001/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json=[
                    {"fieldId": "cf-a", "value": "72"},
                    {"fieldId": "cf-b", "value": "1234"},
                ],
            ),
        )
        client = _make_custom_fields_client()
        result = await client.get_field_values("listing", "lst-001")
        assert len(result) == 2
        assert result[0] == {"fieldId": "cf-a", "value": "72"}
        assert result[1] == {"fieldId": "cf-b", "value": "1234"}

    @respx.mock
    async def test_reservation_extracts_custom_fields(
        self,
    ) -> None:
        """Reservation target extracts customFields from wrapper."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/reservations-v3/res-001/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json={
                    "reservationId": "res-001",
                    "customFields": [
                        {
                            "_id": "abc",
                            "fieldId": "cf-x",
                            "value": "72",
                        },
                        {
                            "_id": "def",
                            "fieldId": "cf-y",
                            "value": "0679",
                        },
                    ],
                },
            ),
        )
        client = _make_custom_fields_client()
        result = await client.get_field_values("reservation", "res-001")
        assert len(result) == 2
        assert result[0] == {"fieldId": "cf-x", "value": "72"}
        assert result[1] == {"fieldId": "cf-y", "value": "0679"}

    @respx.mock
    async def test_listing_empty_array(self) -> None:
        """Listing with no custom fields returns empty list."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/listings/lst-empty/custom-fields",
        ).mock(
            return_value=Response(200, json=[]),
        )
        client = _make_custom_fields_client()
        result = await client.get_field_values("listing", "lst-empty")
        assert result == []

    @respx.mock
    async def test_reservation_empty_custom_fields(self) -> None:
        """Reservation with no custom fields returns empty list."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/reservations-v3/res-empty/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json={
                    "reservationId": "res-empty",
                    "customFields": [],
                },
            ),
        )
        client = _make_custom_fields_client()
        result = await client.get_field_values("reservation", "res-empty")
        assert result == []

    async def test_invalid_target_type_raises_error(
        self,
    ) -> None:
        """Invalid target_type raises GuestyCustomFieldError."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="target"):
            await client.get_field_values("unknown", "x")

    async def test_empty_target_id_raises_error(self) -> None:
        """Empty target_id raises GuestyCustomFieldError."""
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="target_id"):
            await client.get_field_values("listing", "")

    @respx.mock
    async def test_api_error_raises_custom_field_error(
        self,
    ) -> None:
        """Non-2xx status raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/listings/lst-err/custom-fields",
        ).mock(
            return_value=Response(422, json={"error": "Unprocessable"}),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="422"):
            await client.get_field_values("listing", "lst-err")

    @respx.mock
    async def test_auth_error_propagation(self) -> None:
        """403 response propagates as GuestyAuthError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/listings/lst-auth/custom-fields",
        ).mock(
            return_value=Response(403, json={"error": "Forbidden"}),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyAuthError):
            await client.get_field_values("listing", "lst-auth")

    @respx.mock
    async def test_listing_non_list_response_raises_error(
        self,
    ) -> None:
        """Non-list listing response raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/listings/lst-bad/custom-fields",
        ).mock(
            return_value=Response(200, json={"results": []}),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="JSON array"):
            await client.get_field_values("listing", "lst-bad")

    @respx.mock
    async def test_reservation_non_dict_response_raises_error(
        self,
    ) -> None:
        """Non-dict reservation response raises error."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/reservations-v3/res-bad/custom-fields",
        ).mock(
            return_value=Response(200, json=[]),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="JSON object"):
            await client.get_field_values("reservation", "res-bad")

    @respx.mock
    async def test_reservation_non_list_custom_fields(
        self,
    ) -> None:
        """Non-list customFields in reservation raises error."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/reservations-v3/res-nl/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json={
                    "reservationId": "res-nl",
                    "customFields": "bad",
                },
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="JSON array"):
            await client.get_field_values("reservation", "res-nl")

    @respx.mock
    async def test_invalid_json_response_raises_error(
        self,
    ) -> None:
        """Non-JSON response raises GuestyCustomFieldError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/listings/lst-nj/custom-fields",
        ).mock(
            return_value=Response(
                200,
                content=b"not json",
                headers={"content-type": "text/plain"},
            ),
        )
        client = _make_custom_fields_client()
        with pytest.raises(GuestyCustomFieldError, match="JSON"):
            await client.get_field_values("listing", "lst-nj")

    @respx.mock
    async def test_listing_skips_malformed_entries(self) -> None:
        """Listing response skips entries without fieldId/value."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/listings/lst-skip/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json=[
                    {"fieldId": "cf-a", "value": "v1"},
                    {"fieldId": "cf-b"},
                    {"value": "orphan"},
                    "not-a-dict",
                    {"fieldId": "cf-c", "value": "v2"},
                ],
            ),
        )
        client = _make_custom_fields_client()
        result = await client.get_field_values("listing", "lst-skip")
        assert len(result) == 2
        assert result[0]["fieldId"] == "cf-a"
        assert result[1]["fieldId"] == "cf-c"

    @respx.mock
    async def test_reservation_skips_malformed_entries(
        self,
    ) -> None:
        """Reservation response skips entries without fieldId/value."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/reservations-v3/res-skip/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json={
                    "reservationId": "res-skip",
                    "customFields": [
                        {
                            "_id": "a",
                            "fieldId": "cf-x",
                            "value": "v1",
                        },
                        {"_id": "b", "fieldId": "cf-y"},
                        42,
                    ],
                },
            ),
        )
        client = _make_custom_fields_client()
        result = await client.get_field_values("reservation", "res-skip")
        assert len(result) == 1
        assert result[0]["fieldId"] == "cf-x"

    @respx.mock
    async def test_reservation_missing_custom_fields_key(
        self,
    ) -> None:
        """Reservation response without customFields returns empty."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(
            f"{BASE_URL}/reservations-v3/res-no-cf/custom-fields",
        ).mock(
            return_value=Response(
                200,
                json={"reservationId": "res-no-cf"},
            ),
        )
        client = _make_custom_fields_client()
        result = await client.get_field_values("reservation", "res-no-cf")
        assert result == []


class TestReservationCustomFieldsResourceUnchanged:
    """Reservation custom fields stay on v3 (T081, FR-030)."""

    @respx.mock
    async def test_read_uses_the_v3_custom_fields_resource(
        self,
    ) -> None:
        """The read targets RESERVATION_CUSTOM_FIELDS_PATH."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        path = RESERVATION_CUSTOM_FIELDS_PATH.format(
            reservation_id="res-777",
        )
        route = respx.get(f"{BASE_URL}{path}").mock(
            return_value=Response(
                200,
                json={
                    "reservationId": "res-777",
                    "customFields": [
                        {"fieldId": "cf-a", "value": "0042"},
                    ],
                },
            ),
        )
        legacy = respx.route(
            url=f"{BASE_URL}{RESERVATION_BY_ID_LEGACY_PATH.format(reservation_id='res-777')}",
        ).mock(return_value=Response(200, json={}))

        client = _make_custom_fields_client()
        values = await client.get_field_values("reservation", "res-777")

        assert route.called
        assert not legacy.called
        assert values == [{"fieldId": "cf-a", "value": "0042"}]

    @respx.mock
    async def test_write_uses_the_v3_custom_fields_resource(
        self,
    ) -> None:
        """The default write targets the v3 resource verbatim."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        path = RESERVATION_CUSTOM_FIELDS_PATH.format(
            reservation_id="res-777",
        )
        route = respx.put(f"{BASE_URL}{path}").mock(
            return_value=Response(200, json={}),
        )
        legacy = respx.route(
            url=f"{BASE_URL}{RESERVATION_BY_ID_LEGACY_PATH.format(reservation_id='res-777')}",
        ).mock(return_value=Response(200, json={}))

        client = _make_custom_fields_client()
        result = await client.set_field(
            target_type="reservation",
            target_id="res-777",
            field_id="cf-a",
            value="0042",
        )

        assert result.success is True
        assert route.called
        assert not legacy.called
        assert json.loads(route.calls.last.request.content) == {
            "customFields": [{"fieldId": "cf-a", "value": "0042"}],
        }


# ── Phase 7: legacy-routed custom field writes (T129) ──────────────


def _v3_custom_fields_client(
    http: httpx.AsyncClient,
) -> GuestyCustomFieldsClient:
    """Build a custom fields client on the v3 reservation path.

    Args:
        http: Async HTTP client to use.

    Returns:
        A GuestyCustomFieldsClient whose API client has a fallback.
    """
    token_manager = GuestyTokenManager(
        client_id="test-client-id",
        client_secret="test-client-secret",
        http_client=http,
        storage=FakeTokenStorage(),
        refresh_buffer=0,
    )
    api_client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
        reservations_api=RESERVATIONS_API_V3,
    )
    return GuestyCustomFieldsClient(
        api_client=api_client,
        account_id=_FAKE_ACCOUNT_ID,
    )


def _legacy_custom_field_url(reservation_id: str = "res-777") -> str:
    """Build the legacy catch-all URL for a reservation.

    Args:
        reservation_id: The reservation addressed.

    Returns:
        The absolute legacy reservation URL.
    """
    path = RESERVATION_BY_ID_LEGACY_PATH.format(
        reservation_id=reservation_id,
    )
    return f"{BASE_URL}{path}"


class TestLegacyRoutedCustomFieldWrites:
    """Routed custom field writes use the catch-all (T129)."""

    @respx.mock
    async def test_routed_write_uses_the_legacy_catch_all(
        self,
    ) -> None:
        """One legacy PUT, no v3 request, OQ-011 body shape."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        legacy = respx.put(_legacy_custom_field_url()).mock(
            return_value=Response(200, json={}),
        )
        v3 = respx.put(
            f"{BASE_URL}"
            + RESERVATION_CUSTOM_FIELDS_PATH.format(
                reservation_id="res-777",
            ),
        ).mock(return_value=Response(200, json={}))

        http = httpx.AsyncClient()
        try:
            result = await _v3_custom_fields_client(http).set_field(
                target_type="reservation",
                target_id="res-777",
                field_id="cf-a",
                value="0042",
                route=WRITE_ROUTE_LEGACY,
            )
        finally:
            await http.aclose()

        assert result.success is True
        assert not v3.called
        assert len(legacy.calls) == 1
        assert json.loads(legacy.calls[0].request.content) == {
            "customFields": [{"fieldId": "cf-a", "value": "0042"}],
        }

    @respx.mock
    async def test_routed_write_preserves_leading_zeros(self) -> None:
        """A door code keeps every character it was given."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        legacy = respx.put(_legacy_custom_field_url()).mock(
            return_value=Response(200, json={}),
        )

        http = httpx.AsyncClient()
        try:
            await _v3_custom_fields_client(http).set_field(
                target_type="reservation",
                target_id="res-777",
                field_id="cf-a",
                value="000420",
                route=WRITE_ROUTE_LEGACY,
            )
        finally:
            await http.aclose()

        body = json.loads(legacy.calls[0].request.content)
        assert body["customFields"][0]["value"] == "000420"

    @respx.mock
    async def test_routed_listing_write_is_refused(self) -> None:
        """Only reservations may be routed to legacy (FR-059)."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        legacy = respx.route(method="PUT").mock(
            return_value=Response(200, json={}),
        )

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyCustomFieldError) as exc_info:
                await _v3_custom_fields_client(http).set_field(
                    target_type="listing",
                    target_id="lst-1",
                    field_id="cf-a",
                    value="x",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        assert not legacy.called
        assert "reservation" in str(exc_info.value)

    @respx.mock
    async def test_routed_write_is_issued_exactly_once(self) -> None:
        """A rate-limited routed write is never retried (FR-063)."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        legacy = respx.put(_legacy_custom_field_url()).mock(
            return_value=Response(429, json={"error": "slow down"}),
        )

        http = httpx.AsyncClient()
        try:
            with pytest.raises(GuestyCustomFieldError):
                await _v3_custom_fields_client(http).set_field(
                    target_type="reservation",
                    target_id="res-777",
                    field_id="cf-a",
                    value="0042",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        assert len(legacy.calls) == 1

    @respx.mock
    async def test_retirement_disables_routing_and_names_date(
        self,
    ) -> None:
        """A 410 self-disables and the refusal names the date."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        legacy = respx.put(_legacy_custom_field_url()).mock(
            return_value=Response(410, json={"error": "gone"}),
        )

        http = httpx.AsyncClient()
        try:
            client = _v3_custom_fields_client(http)
            with pytest.raises(GuestyCustomFieldError) as first:
                await client.set_field(
                    target_type="reservation",
                    target_id="res-777",
                    field_id="cf-a",
                    value="0042",
                    route=WRITE_ROUTE_LEGACY,
                )
            assert "2027-03-01" in str(first.value)
            assert client._api_client.legacy_write_routing_available is False

            with pytest.raises(GuestyCustomFieldError) as second:
                await client.set_field(
                    target_type="reservation",
                    target_id="res-777",
                    field_id="cf-a",
                    value="0042",
                    route=WRITE_ROUTE_LEGACY,
                )
        finally:
            await http.aclose()

        assert "2027-03-01" in str(second.value)
        assert len(legacy.calls) == 1
