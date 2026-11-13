# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Exception hierarchy for the Guesty API client."""

from __future__ import annotations

from datetime import datetime

from custom_components.guesty.api.const import MAX_ERROR_BODY_EXCERPT


class GuestyApiError(Exception):
    """Base exception for all Guesty API errors.

    Attributes:
        message: Human-readable error description.
    """

    def __init__(self, message: str) -> None:
        """Initialize GuestyApiError.

        Args:
            message: Human-readable error description.
        """
        self.message = message
        super().__init__(message)


class GuestyAuthError(GuestyApiError):
    """Authentication error: invalid credentials, expired token, 401/403."""


class GuestyRateLimitError(GuestyApiError):
    """Rate limit exceeded: HTTP 429 or token request limit exceeded.

    Attributes:
        retry_after: Seconds to wait before retrying, if known.
        reset_at: Datetime when the rate limit window resets, if known.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        reset_at: datetime | None = None,
    ) -> None:
        """Initialize GuestyRateLimitError.

        Args:
            message: Human-readable error description.
            retry_after: Seconds to wait before retrying.
            reset_at: Datetime when the rate limit window resets.
        """
        super().__init__(message)
        self.retry_after = retry_after
        self.reset_at = reset_at


class GuestyConnectionError(GuestyApiError):
    """Network-level failure: DNS, TCP, TLS, or timeout."""


class GuestyResponseError(GuestyApiError):
    """Unexpected response format: missing fields, invalid JSON."""


class GuestyFilterRejectedError(GuestyResponseError):
    """Guesty rejected the query filters of a search request.

    Raised on HTTP 400 from the reservations v3 search. The request
    is never retried with the filters removed: an unfiltered retry
    would silently return the wrong reservations (issue #204).

    Attributes:
        endpoint: API path that rejected the request.
        status_code: HTTP status returned by the API.
        body_excerpt: Truncated excerpt of the response body.
    """

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        status_code: int | None = None,
        body_excerpt: str | None = None,
    ) -> None:
        """Initialize GuestyFilterRejectedError.

        Args:
            message: Human-readable error description.
            endpoint: API path that rejected the request.
            status_code: HTTP status returned by the API.
            body_excerpt: Response body, truncated when oversized.
        """
        self.endpoint = endpoint
        self.status_code = status_code
        self.body_excerpt = _truncate_excerpt(body_excerpt)

        detail = [message]
        if endpoint is not None:
            detail.append(f"endpoint={endpoint}")
        if status_code is not None:
            detail.append(f"status={status_code}")
        if self.body_excerpt:
            detail.append(f"body={self.body_excerpt}")
        super().__init__("; ".join(detail))


def _truncate_excerpt(body: str | None) -> str | None:
    """Truncate a response body to the error excerpt limit.

    Args:
        body: The response body, or None when unavailable.

    Returns:
        The body unchanged, an ellipsis-suffixed excerpt, or None.
    """
    if body is None:
        return None
    if len(body) <= MAX_ERROR_BODY_EXCERPT:
        return body
    return f"{body[:MAX_ERROR_BODY_EXCERPT]}..."


class GuestyMessageError(GuestyApiError):
    """Messaging delivery failure with reservation context.

    Attributes:
        reservation_id: The targeted reservation for context.
        available_channels: Available channels for channel errors.
    """

    def __init__(
        self,
        message: str,
        reservation_id: str | None = None,
        available_channels: tuple[str, ...] | None = None,
    ) -> None:
        """Initialize GuestyMessageError.

        Args:
            message: Human-readable error description.
            reservation_id: The targeted reservation for context.
            available_channels: Available channels for errors.
        """
        super().__init__(message)
        self.reservation_id = reservation_id
        self.available_channels = available_channels


class GuestyCustomFieldError(GuestyApiError):
    """Custom field operation failure with entity context.

    Attributes:
        target_type: Entity type ('listing' or 'reservation').
        target_id: The targeted entity identifier.
        field_id: The custom field identifier.
    """

    def __init__(
        self,
        message: str,
        target_type: str | None = None,
        target_id: str | None = None,
        field_id: str | None = None,
    ) -> None:
        """Initialize GuestyCustomFieldError.

        Args:
            message: Human-readable error description.
            target_type: Entity type for context.
            target_id: The targeted entity identifier.
            field_id: The custom field identifier.
        """
        super().__init__(message)
        self.target_type = target_type
        self.target_id = target_id
        self.field_id = field_id


class GuestyActionError(GuestyApiError):
    """Action operation failure with debugging context.

    Attributes:
        target_id: The targeted resource identifier.
        action_type: The action that failed (e.g. 'add_note').
    """

    def __init__(
        self,
        message: str,
        target_id: str | None = None,
        action_type: str | None = None,
    ) -> None:
        """Initialize GuestyActionError.

        Args:
            message: Human-readable error description.
            target_id: The targeted resource identifier.
            action_type: The action that failed.
        """
        super().__init__(message)
        self.target_id = target_id
        self.action_type = action_type
