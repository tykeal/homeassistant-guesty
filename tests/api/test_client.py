# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyApiClient including test_connection and listings.

Also includes Phase 4 (T025) transient failure retry tests.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import traceback
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import (
    GuestyApiClient,
    private_http_logging,
    resolve_reservations_api,
)
from custom_components.guesty.api.const import (
    BASE_URL,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyPermissionError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import (
    CachedToken,
    GuestyReservation,
    ReservationFetchResult,
)
from custom_components.guesty.api.reservations_legacy import (
    LegacyReservationSource,
)
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_TOKEN,
    FakeTokenStorage,
    make_token_response,
)


def _make_client(
    storage: FakeTokenStorage | None = None,
    reservations_api: str | None = None,
) -> tuple[GuestyApiClient, GuestyTokenManager, FakeTokenStorage]:
    """Create a GuestyApiClient with test defaults.

    Args:
        storage: Optional FakeTokenStorage to use.
        reservations_api: Optional reservation path selector.

    Returns:
        Tuple of (api_client, token_manager, storage).
    """
    if storage is None:
        storage = FakeTokenStorage()
    http = httpx.AsyncClient()
    token_manager = GuestyTokenManager(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        http_client=http,
        storage=storage,
        refresh_buffer=0,
    )
    kwargs: dict[str, Any] = {}
    if reservations_api is not None:
        kwargs["reservations_api"] = reservations_api
    client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
        **kwargs,
    )
    return client, token_manager, storage


class TestRecoveredWriteTransport:
    """Safety of a single attempted recovered write request."""

    async def test_inherited_logging_scope_expires_with_parent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A child task or callback must not inherit suppression permanently."""
        logger = logging.getLogger("httpcore.http11")
        release = asyncio.Event()
        callback_done = asyncio.Event()

        async def child() -> None:
            """Log only after the parent has left its privacy scope."""
            await release.wait()
            logger.debug("child-after-scope")

        def callback() -> None:
            """A scheduled callback carries the same copied context."""
            logger.debug("callback-after-scope")
            callback_done.set()

        with caplog.at_level(logging.DEBUG):
            with private_http_logging(True):
                logger.debug("private-in-scope")
                task = asyncio.create_task(child())
                asyncio.get_running_loop().call_soon(callback)
            release.set()
            await task
            await asyncio.wait_for(callback_done.wait(), 2)
        assert "private-in-scope" not in caplog.text
        assert "child-after-scope" in caplog.text
        assert "callback-after-scope" in caplog.text

    async def test_child_privacy_scope_survives_parent_exit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A child with its own active scope stays private after the parent exits."""
        logger = logging.getLogger("httpcore.http11")
        child_started = asyncio.Event()
        release = asyncio.Event()

        async def child() -> None:
            """Keep an independent nested privacy scope until released."""
            with private_http_logging(True):
                child_started.set()
                await release.wait()
                logger.debug("child-private")
            logger.debug("child-unprotected")

        with caplog.at_level(logging.DEBUG):
            with private_http_logging(True):
                task = asyncio.create_task(child())
                await asyncio.wait_for(child_started.wait(), 2)
            release.set()
            await task
        assert "child-private" not in caplog.text
        assert "child-unprotected" in caplog.text

    async def test_copied_inner_scope_respects_active_outer_scope(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A child stays private while the enclosing parent scope remains active."""
        logger = logging.getLogger("httpcore.http11")
        release = asyncio.Event()

        async def child() -> None:
            """Run after the nested scope ends but before the outer scope ends."""
            await release.wait()
            logger.debug("outer-private")

        with caplog.at_level(logging.DEBUG):
            with private_http_logging(True):
                with private_http_logging(True):
                    task = asyncio.create_task(child())
                release.set()
                await task
            logger.debug("after-outer")
        assert "outer-private" not in caplog.text
        assert "after-outer" in caplog.text

    async def test_invalid_bearer_header_is_redacted(
        self, caplog: pytest.LogCaptureFixture, socket_enabled: None
    ) -> None:
        """Real h11 validation cannot reveal a malformed bearer credential."""
        secret = "private-malformed-bearer-sentinel"
        connected = asyncio.Event()
        socket_data: list[bytes] = []

        async def accept(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            """Record bytes received by the local HTTP peer."""
            connected.set()
            try:
                socket_data.append(await reader.read(4096))
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(accept, "127.0.0.1", 0)
        assert server.sockets is not None
        port = server.sockets[0].getsockname()[1]
        client, manager, _ = _make_client()
        client._base_url = f"http://127.0.0.1:{port}"
        try:
            with (
                caplog.at_level(logging.DEBUG),
                patch.object(manager, "get_token", return_value=f"{secret}\ninvalid"),
                patch.object(
                    client._http, "request", wraps=client._http.request
                ) as put,
                pytest.raises(Exception) as raised,
            ):
                await client._request("PUT", "/reservations/test", recovery_write=True)
            assert secret not in caplog.text
            assert isinstance(raised.value, GuestyAuthError)
            assert getattr(raised.value, "stage", None) == "token"
            assert "write was not sent" in str(raised.value).lower()
            assert secret not in str(raised.value)
            assert secret not in "".join(traceback.format_exception(raised.value))
            assert secret not in caplog.text
            put.assert_not_awaited()
            assert not connected.is_set()
            assert not socket_data
        finally:
            await client._http.aclose()
            server.close()
            await server.wait_closed()

    async def test_real_proxy_error_is_redacted(
        self, caplog: pytest.LogCaptureFixture, socket_enabled: None
    ) -> None:
        """Real proxy response data must not leak via HTTPcore debug logs."""
        secret = "private-proxy-sentinel"
        requests: list[bytes] = []
        finished = asyncio.Event()

        async def reject_proxy(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            """Reject a real HTTPS CONNECT with an untrusted reason phrase."""
            try:
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(
                    f"HTTP/1.1 407 {secret}\r\nContent-Length: 0\r\n\r\n".encode()
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        server = await asyncio.start_server(reject_proxy, "127.0.0.1", 0)
        assert server.sockets is not None
        port = server.sockets[0].getsockname()[1]
        client, manager, _ = _make_client()
        await client._http.aclose()
        client._http = httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}")
        try:
            with (
                caplog.at_level(logging.DEBUG),
                patch.object(manager, "get_token", return_value="token"),
                patch.object(
                    client._http, "request", wraps=client._http.request
                ) as put,
                pytest.raises(Exception) as raised,
            ):
                await client._request("PUT", "/reservations/test", recovery_write=True)
            assert secret not in caplog.text
            assert isinstance(raised.value, GuestyConnectionError)
            assert getattr(raised.value, "stage", None) == "write"
            assert "write was not sent" in str(raised.value).lower()
            assert "not retried" in str(raised.value).lower()
            assert secret not in str(raised.value)
            assert secret not in "".join(traceback.format_exception(raised.value))
            assert secret not in caplog.text
            put.assert_awaited_once()
            await asyncio.wait_for(finished.wait(), 2)
            assert len(requests) == 1
        finally:
            await client._http.aclose()
            server.close()
            await server.wait_closed()

    async def test_real_token_proxy_error_is_redacted(
        self, caplog: pytest.LogCaptureFixture, socket_enabled: None
    ) -> None:
        """A proxy rejection while acquiring the token is safe at DEBUG."""
        secret = "private-token-proxy-sentinel"
        requests: list[bytes] = []
        finished = asyncio.Event()

        async def reject_proxy(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            """Reject the local HTTPS CONNECT using an untrusted reason."""
            try:
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(
                    f"HTTP/1.1 407 {secret}\r\nContent-Length: 0\r\n\r\n".encode()
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        server = await asyncio.start_server(reject_proxy, "127.0.0.1", 0)
        assert server.sockets is not None
        port = server.sockets[0].getsockname()[1]
        client, manager, _ = _make_client()
        await client._http.aclose()
        client._http = httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}")
        manager._http = client._http
        try:
            with (
                caplog.at_level(logging.DEBUG),
                pytest.raises(GuestyApiError) as raised,
            ):
                await client._request("PUT", "/reservations/test", recovery_write=True)
            assert secret not in caplog.text
            assert secret not in "".join(traceback.format_exception(raised.value))
            assert getattr(raised.value, "stage", None) == "token"
            assert "write was not sent" in str(raised.value).lower()
            await asyncio.wait_for(finished.wait(), 2)
            assert len(requests) == 1
            assert requests[0].startswith(b"CONNECT ")
        finally:
            await client._http.aclose()
            server.close()
            await server.wait_closed()

    async def test_unrelated_http_debug_logs_survive_concurrent_recovery(
        self, caplog: pytest.LogCaptureFixture, socket_enabled: None
    ) -> None:
        """Transport filtering is scoped to the recovery task, not global."""
        secret = "private-recovery-proxy-sentinel"
        ordinary = "ordinary-proxy-log-control"
        both_started = asyncio.Event()
        release = asyncio.Event()
        requests: list[bytes] = []

        async def reject_proxy(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            """Keep both proxy connections in flight at the same time."""
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                requests.append(request)
                if len(requests) == 2:
                    both_started.set()
                await release.wait()
                reason = secret if b"recovered.invalid" in request else ordinary
                writer.write(
                    f"HTTP/1.1 407 {reason}\r\nContent-Length: 0\r\n\r\n".encode()
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(reject_proxy, "127.0.0.1", 0)
        assert server.sockets is not None
        port = server.sockets[0].getsockname()[1]
        recovered, recovered_manager, _ = _make_client()
        normal, normal_manager, _ = _make_client()
        await recovered._http.aclose()
        await normal._http.aclose()
        recovered._http = httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}")
        normal._http = httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}")
        recovered._base_url = "https://recovered.invalid"
        normal._base_url = "https://ordinary.invalid"
        tasks: list[asyncio.Task[httpx.Response]] = []
        try:
            with (
                caplog.at_level(logging.DEBUG),
                patch.object(recovered_manager, "get_token", return_value="token"),
                patch.object(normal_manager, "get_token", return_value="token"),
            ):
                tasks = [
                    asyncio.create_task(
                        recovered._request("PUT", "/test", recovery_write=True)
                    ),
                    asyncio.create_task(normal._request("PUT", "/test")),
                ]
                await asyncio.wait_for(both_started.wait(), 2)
                release.set()
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), 2
                )
            assert isinstance(results[0], GuestyConnectionError)
            assert isinstance(results[1], httpx.ProxyError)
            assert secret not in caplog.text
            assert ordinary in caplog.text
            assert len(requests) == 2
        finally:
            release.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await recovered._http.aclose()
            await normal._http.aclose()
            server.close()
            await server.wait_closed()

    @respx.mock
    @pytest.mark.parametrize("failure", ["403", "429", "500", "exhausted", "transport"])
    async def test_real_token_failure_is_safe_and_unsent(
        self, failure: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Token failures cannot be mistaken for an attempted reservation PUT."""
        secret = "private-token-response-sentinel"
        client, manager, _ = _make_client()
        token = respx.post(TOKEN_URL)
        if failure == "transport":
            token.mock(side_effect=httpx.ReadError(secret))
        else:
            token.mock(
                return_value=Response(
                    429 if failure == "exhausted" else int(failure), text=secret
                )
            )
        if failure == "exhausted":
            manager._request_count = 5
            manager._window_start = datetime.now(UTC)
        put = respx.put(f"{BASE_URL}/reservations/test").mock(
            return_value=Response(200)
        )
        try:
            with caplog.at_level(logging.DEBUG), pytest.raises(Exception) as raised:
                await client._request("PUT", "/reservations/test", recovery_write=True)
            assert isinstance(raised.value, GuestyApiError)
            assert getattr(raised.value, "stage", None) == "token"
            assert "write was not sent" in str(raised.value).lower()
            assert "may have reached" not in str(raised.value).lower()
            assert not put.called
            assert len(token.calls) == (0 if failure == "exhausted" else 1)
            assert secret not in str(raised.value)
            assert secret not in "".join(traceback.format_exception(raised.value))
            assert secret not in caplog.text
        finally:
            await client._http.aclose()

    async def test_token_storage_failure_is_safe_and_unsent(self) -> None:
        """A storage failure during token acquisition never reaches the PUT."""
        secret = "private-token-storage-sentinel"
        client, _, storage = _make_client()
        try:
            with (
                patch.object(
                    storage, "load_request_count", side_effect=OSError(secret)
                ),
                patch.object(client._http, "request", new_callable=AsyncMock) as put,
                pytest.raises(Exception) as raised,
            ):
                await client._request("PUT", "/reservations/test", recovery_write=True)
            assert isinstance(raised.value, GuestyApiError)
            assert getattr(raised.value, "stage", None) == "token"
            assert "write was not sent" in str(raised.value).lower()
            assert secret not in "".join(traceback.format_exception(raised.value))
            put.assert_not_awaited()
        finally:
            await client._http.aclose()

    @staticmethod
    def _recovery_options(client: GuestyApiClient) -> dict[str, Any]:
        """Use the pre-send hook when available, without requiring its existence."""
        options: dict[str, Any] = {"single_attempt": True}
        if "before_send" in inspect.signature(client._request).parameters:
            options["before_send"] = lambda: None
        if "recovery_write" in inspect.signature(client._request).parameters:
            options["recovery_write"] = True
        return options

    async def test_permission_rejection_is_distinct_from_authentication(self) -> None:
        """A 403 is an actionable permission failure, distinct from a 401."""
        client, manager, _ = _make_client()
        request = AsyncMock(
            side_effect=[
                Response(403, text="private-response-sentinel"),
                Response(401, text="private-response-sentinel"),
            ]
        )
        try:
            with (
                patch.object(manager, "get_token", return_value="token"),
                patch.object(client._http, "request", request),
            ):
                with pytest.raises(GuestyAuthError) as permission:
                    await client._request(
                        "PUT", "/reservations/test", single_attempt=True
                    )
                with pytest.raises(GuestyAuthError) as authentication:
                    await client._request(
                        "PUT", "/reservations/test", _retried_auth=True
                    )
            assert request.await_count == 2
            assert "private-response-sentinel" not in str(permission.value)
            assert "private-response-sentinel" not in str(authentication.value)
            assert (
                type(permission.value) is not type(authentication.value)
                or getattr(permission.value, "status_code", None) == 403
                or getattr(permission.value, "category", None) == "permission"
            )
        finally:
            await client._http.aclose()

    @pytest.mark.parametrize(
        "error_type",
        [
            httpx.ReadError,
            httpx.WriteError,
            httpx.RemoteProtocolError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.LocalProtocolError,
        ],
    )
    async def test_post_send_transport_failure_is_typed_and_safe(
        self, error_type: type[httpx.TransportError]
    ) -> None:
        """A transport failure after entering the request is not retried or leaked."""
        client, manager, _ = _make_client()
        request = AsyncMock(side_effect=error_type("private-response-sentinel"))
        with (
            patch.object(manager, "get_token", return_value="token"),
            patch.object(client._http, "request", request),
            pytest.raises(GuestyConnectionError) as excinfo,
        ):
            await client._request(
                "PUT", "/reservations/test", **self._recovery_options(client)
            )
        request.assert_awaited_once()
        assert "private-response-sentinel" not in str(excinfo.value)
        assert getattr(excinfo.value, "stage", None) == "write"
        assert getattr(excinfo.value, "category", None) == "connection"
        assert "may have reached guesty" in str(excinfo.value).lower()
        assert "not retried" in str(excinfo.value).lower()
        await client._http.aclose()

    @pytest.mark.parametrize(
        "error_type",
        [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError],
    )
    async def test_presend_transport_failure_is_typed_and_safe(
        self, error_type: type[httpx.TransportError], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Connection setup failures state that no recovered write was sent."""
        client, manager, _ = _make_client()
        secret = "private-transport-sentinel"
        request = AsyncMock(side_effect=error_type(secret))
        try:
            with (
                caplog.at_level(logging.DEBUG),
                patch.object(manager, "get_token", return_value="token"),
                patch.object(client._http, "request", request),
                pytest.raises(GuestyConnectionError) as raised,
            ):
                await client._request("PUT", "/reservations/test", recovery_write=True)
            request.assert_awaited_once()
            assert getattr(raised.value, "stage", None) == "write"
            assert getattr(raised.value, "category", None) == "connection"
            assert "write was not sent" in str(raised.value).lower()
            assert "not retried" in str(raised.value).lower()
            assert secret not in str(raised.value)
            assert secret not in "".join(traceback.format_exception(raised.value))
            assert secret not in caplog.text
        finally:
            await client._http.aclose()

    async def test_token_failure_is_safely_identified_as_pre_send(self) -> None:
        """A failed token lookup reports that no write was sent."""
        client, manager, _ = _make_client()
        request = AsyncMock()
        with (
            patch.object(
                manager,
                "get_token",
                side_effect=GuestyAuthError("private-response-sentinel"),
            ),
            patch.object(client._http, "request", request),
            pytest.raises(GuestyApiError) as excinfo,
        ):
            await client._request(
                "PUT", "/reservations/test", **self._recovery_options(client)
            )
        request.assert_not_awaited()
        assert "private-response-sentinel" not in str(excinfo.value)
        assert getattr(excinfo.value, "stage", None) == "token"
        assert getattr(excinfo.value, "category", None) == "authentication"
        assert "write was not sent" in str(excinfo.value).lower()
        await client._http.aclose()

    async def test_token_permission_failure_is_pre_send(self) -> None:
        """Token permission failure remains distinct and sends no write."""
        client, manager, _ = _make_client()
        request = AsyncMock()
        with (
            patch.object(
                manager,
                "get_token",
                side_effect=GuestyPermissionError("private-response-sentinel"),
            ),
            patch.object(client._http, "request", request),
            pytest.raises(GuestyPermissionError) as excinfo,
        ):
            await client._request(
                "PUT", "/reservations/test", **self._recovery_options(client)
            )
        request.assert_not_awaited()
        assert "private-response-sentinel" not in str(excinfo.value)
        assert getattr(excinfo.value, "stage", None) == "token"
        assert getattr(excinfo.value, "category", None) == "permission"
        assert "write was not sent" in str(excinfo.value).lower()
        await client._http.aclose()

    async def test_token_connection_failure_is_pre_send(self) -> None:
        """A failed token connection reports that the write was not sent."""
        client, manager, _ = _make_client()
        request = AsyncMock()
        with (
            patch.object(
                manager,
                "get_token",
                side_effect=GuestyConnectionError("private-response-sentinel"),
            ),
            patch.object(client._http, "request", request),
            pytest.raises(GuestyConnectionError) as excinfo,
        ):
            await client._request(
                "PUT", "/reservations/test", **self._recovery_options(client)
            )
        request.assert_not_awaited()
        assert "private-response-sentinel" not in str(excinfo.value)
        assert getattr(excinfo.value, "stage", None) == "token"
        assert getattr(excinfo.value, "category", None) == "connection"
        assert "write was not sent" in str(excinfo.value).lower()
        await client._http.aclose()

    async def test_single_attempt_401_reports_safe_authentication(self) -> None:
        """A recovered PUT 401 preserves authentication as an actionable category."""
        client, manager, _ = _make_client()
        request = AsyncMock(
            return_value=Response(401, text="private-response-sentinel")
        )
        with (
            patch.object(manager, "get_token", return_value="token"),
            patch.object(client._http, "request", request),
            pytest.raises(GuestyAuthError) as excinfo,
        ):
            await client._request(
                "PUT", "/reservations/test", **self._recovery_options(client)
            )
        request.assert_awaited_once()
        assert "private-response-sentinel" not in str(excinfo.value)
        await client._http.aclose()

    async def test_recovered_403_is_a_safe_write_permission_error(self) -> None:
        """A rejected recovered PUT reports permission and its write stage."""
        client, manager, _ = _make_client()
        request = AsyncMock(
            return_value=Response(403, text="private-response-sentinel")
        )
        with (
            patch.object(manager, "get_token", return_value="token"),
            patch.object(client._http, "request", request),
            pytest.raises(GuestyPermissionError) as excinfo,
        ):
            await client._request(
                "PUT", "/reservations/test", **self._recovery_options(client)
            )
        request.assert_awaited_once()
        assert "private-response-sentinel" not in str(excinfo.value)
        assert getattr(excinfo.value, "stage", None) == "write"
        assert getattr(excinfo.value, "category", None) == "permission"
        await client._http.aclose()

    @pytest.mark.parametrize("status", [401, 403])
    async def test_recovered_auth_rejection_reports_no_retry(self, status: int) -> None:
        """A returned authentication rejection is distinguished from a retry."""
        client, manager, _ = _make_client()
        request = AsyncMock(return_value=Response(status, text="private-sentinel"))
        with (
            patch.object(manager, "get_token", return_value="token"),
            patch.object(client._http, "request", request),
            pytest.raises(GuestyAuthError) as raised,
        ):
            await client._request(
                "PUT", "/reservations/test", **self._recovery_options(client)
            )
        request.assert_awaited_once()
        assert "not retried" in str(raised.value).lower()
        assert "private-sentinel" not in str(raised.value)
        await client._http.aclose()

    async def test_normal_transport_error_policy_is_unchanged(self) -> None:
        """A non-recovery ReadError propagates with its previous retry behavior."""
        client, manager, _ = _make_client()
        request = AsyncMock(side_effect=httpx.ReadError("connection"))
        with (
            patch.object(manager, "get_token", return_value="token"),
            patch.object(client._http, "request", request),
            pytest.raises(httpx.ReadError),
        ):
            await client._request("PUT", "/reservations/test")
        request.assert_awaited_once()
        await client._http.aclose()

    async def test_cancelled_token_lookup_is_not_wrapped(self) -> None:
        """Cancellation remains cancellation and does not send the write."""
        client, manager, _ = _make_client()
        request = AsyncMock()
        with (
            patch.object(manager, "get_token", side_effect=asyncio.CancelledError),
            patch.object(client._http, "request", request),
            pytest.raises(asyncio.CancelledError),
        ):
            await client._request(
                "PUT", "/reservations/test", **self._recovery_options(client)
            )
        request.assert_not_awaited()
        await client._http.aclose()

    async def test_guard_after_token_can_prevent_dispatch(self) -> None:
        """A recheck after token acquisition can stop the PUT before dispatch."""
        client, manager, _ = _make_client()
        token = AsyncMock(return_value="token")
        request = AsyncMock(return_value=Response(200))

        def recheck() -> None:
            """Model an identity guard which changed while token was awaited."""
            raise GuestyResponseError("guard changed")

        options = self._recovery_options(client)
        if "before_send" in options:
            options["before_send"] = recheck
        with (
            patch.object(manager, "get_token", token),
            patch.object(client._http, "request", request),
            pytest.raises(GuestyResponseError, match="guard changed"),
        ):
            await client._request("PUT", "/reservations/test", **options)
        token.assert_awaited_once()
        request.assert_not_awaited()
        await client._http.aclose()


class TestTestConnection:
    """Tests for GuestyApiClient.test_connection()."""

    @respx.mock
    async def test_successful_connection(self) -> None:
        """test_connection() succeeds with valid credentials."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(200, json={"results": []}),
        )
        client, _, _ = _make_client()
        result = await client.test_connection()
        assert result is True

    @respx.mock
    async def test_auth_error_propagation(self) -> None:
        """test_connection() propagates GuestyAuthError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                401,
                json={"error": "invalid_client"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyAuthError):
            await client.test_connection()

    @respx.mock
    async def test_connection_error_propagation(self) -> None:
        """test_connection() propagates GuestyConnectionError."""
        respx.post(TOKEN_URL).mock(
            side_effect=httpx.ConnectError("refused"),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyConnectionError):
            await client.test_connection()

    @respx.mock
    async def test_bearer_header_included(self) -> None:
        """Requests include Authorization: Bearer header."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        route = respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(200, json={"results": []}),
        )
        client, _, _ = _make_client()
        await client.test_connection()
        request = route.calls[0].request
        assert request.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"


class TestReactiveRefresh:
    """Tests for reactive 401 refresh in _request()."""

    @respx.mock
    async def test_401_triggers_refresh_and_retry(self) -> None:
        """A 401 response triggers token invalidation and retry."""
        # First token request succeeds
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        # First API call returns 401, retry succeeds
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(401, json={"error": "Unauthorized"}),
            Response(200, json={"results": []}),
        ]

        storage = FakeTokenStorage()
        client, manager, _ = _make_client(storage)
        # Pre-seed a token so the first call uses it
        manager.seed_token(
            CachedToken(
                access_token="old-token",
                token_type="Bearer",
                expires_in=86400,
                scope="open-api",
                issued_at=datetime.now(UTC),
            ),
        )

        result = await client.test_connection()
        assert result is True

    @respx.mock
    async def test_double_401_raises_auth_error(self) -> None:
        """Two consecutive 401s raise GuestyAuthError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                401,
                json={"error": "Unauthorized"},
            ),
        )

        client, _, _ = _make_client()
        with pytest.raises(GuestyAuthError, match="after token refresh"):
            await client.test_connection()


class TestRateLimitBackoff:
    """Tests for HTTP 429 exponential backoff."""

    @respx.mock
    async def test_429_triggers_retry(self) -> None:
        """429 response triggers retry with backoff."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(429, headers={"Retry-After": "0"}),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()
        assert result is True

    @respx.mock
    async def test_429_retry_after_header_respected(self) -> None:
        """Retry-After header is used for backoff delay."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(429, headers={"Retry-After": "5"}),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            await client.test_connection()

        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert delay == 5.0

    @respx.mock
    async def test_429_max_retries_raises(self) -> None:
        """Exhausted retries raise GuestyRateLimitError."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyRateLimitError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                429,
                headers={"Retry-After": "0"},
            ),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyRateLimitError, match="max retries"),
        ):
            await client.test_connection()

    @respx.mock
    async def test_429_without_retry_after_uses_jitter(self) -> None:
        """429 without Retry-After header uses jitter backoff."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(429),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            result = await client.test_connection()

        assert result is True
        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert delay >= 0.1

    @respx.mock
    async def test_429_max_retries_no_retry_after(self) -> None:
        """Max retries with no Retry-After raises with None."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyRateLimitError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(429),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyRateLimitError) as exc_info,
        ):
            await client.test_connection()
        assert exc_info.value.retry_after is None

    @respx.mock
    async def test_429_invalid_retry_after_header(self) -> None:
        """Invalid Retry-After header falls back to jitter."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(429, headers={"Retry-After": "not-a-number"}),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            result = await client.test_connection()

        assert result is True
        mock_sleep.assert_called_once()

    @respx.mock
    async def test_429_negative_retry_after_clamped(self) -> None:
        """Negative Retry-After is clamped to minimum delay."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "-1"},
            ),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            result = await client.test_connection()

        assert result is True
        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert delay >= 0.1


class TestConnectionTestFailure:
    """Tests for test_connection non-success responses."""

    @respx.mock
    async def test_non_success_raises_response_error(self) -> None:
        """test_connection raises on non-success status."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(422, text="Unprocessable Entity"),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyResponseError,
                match="Connection test failed: status 422",
            ),
        ):
            await client.test_connection()


class TestRequestNetworkError:
    """Tests for network errors during API requests."""

    @respx.mock
    async def test_connect_error_in_request(self) -> None:
        """ConnectError during API call raises GuestyConnectionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.ConnectError("refused"),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyConnectionError),
        ):
            await client.test_connection()

    @respx.mock
    async def test_timeout_in_request(self) -> None:
        """TimeoutException during API call raises error."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.TimeoutException("timed out"),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyConnectionError),
        ):
            await client.test_connection()


class TestForbiddenResponse:
    """Tests for HTTP 403 Forbidden responses."""

    @respx.mock
    async def test_403_raises_auth_error(self) -> None:
        """403 response raises GuestyAuthError."""
        from custom_components.guesty.api.exceptions import (
            GuestyAuthError as AuthError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                403,
                json={"error": "Forbidden"},
            ),
        )

        client, _, _ = _make_client()
        with pytest.raises(AuthError, match="Insufficient permissions"):
            await client.test_connection()


# ── get_listings() Tests (T006) ─────────────────────────────────────


def _make_listing_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API listing dictionary with defaults.

    Args:
        **overrides: Fields to override on the default listing.

    Returns:
        Dictionary matching the Guesty listings API format.
    """
    defaults: dict[str, Any] = {
        "_id": "507f1f77bcf86cd799439011",
        "title": "Beach House",
        "nickname": "Beach Alt",
        "listed": True,
        "active": True,
        "address": {
            "full": "123 Beach Rd, Miami, FL 33139, USA",
        },
        "propertyType": "apartment",
        "roomType": "Entire home/apartment",
        "type": "SINGLE",
        "bedrooms": 2,
        "bathrooms": 1.5,
        "accommodates": 5,
        "timezone": "America/New_York",
        "defaultCheckInTime": "15:00",
        "defaultCheckOutTime": "11:00",
        "tags": ["pet-friendly"],
        "customFields": [
            {"fieldId": "cf_region", "value": "southeast"},
        ],
    }
    defaults.update(overrides)
    return defaults


def _make_page_response(
    listings: list[dict[str, Any]],
    *,
    count: int = 1,
    limit: int = 100,
    skip: int = 0,
) -> dict[str, Any]:
    """Create a Guesty listings page response.

    Args:
        listings: Array of listing dictionaries.
        count: Total count metadata.
        limit: Page size.
        skip: Offset.

    Returns:
        Dictionary matching the listings endpoint format.
    """
    return {
        "results": listings,
        "count": count,
        "limit": limit,
        "skip": skip,
    }


class TestGetListings:
    """Tests for GuestyApiClient.get_listings()."""

    @respx.mock
    async def test_single_page_fetch(self) -> None:
        """Single page with fewer results than limit stops."""
        from custom_components.guesty.api.const import (
            LISTINGS_FIELDS,
            LISTINGS_PAGE_SIZE,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings = [_make_listing_dict(_id=f"id-{i}") for i in range(5)]
        route = respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json=_make_page_response(
                    listings,
                    count=5,
                    limit=100,
                    skip=0,
                ),
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_listings()
        assert len(result) == 5
        assert result[0].id == "id-0"

        assert len(route.calls) == 1
        params = route.calls[0].request.url.params
        assert params["limit"] == str(LISTINGS_PAGE_SIZE)
        assert params["skip"] == "0"
        assert params["fields"] == " ".join(LISTINGS_FIELDS)

    @respx.mock
    async def test_multi_page_pagination(self) -> None:
        """Paginate through multiple pages to partial page."""
        from custom_components.guesty.api.const import (
            LISTINGS_FIELDS,
            LISTINGS_PAGE_SIZE,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        page1 = [_make_listing_dict(_id=f"p1-{i}") for i in range(LISTINGS_PAGE_SIZE)]
        page2 = [_make_listing_dict(_id=f"p2-{i}") for i in range(LISTINGS_PAGE_SIZE)]
        page3 = [_make_listing_dict(_id=f"p3-{i}") for i in range(30)]

        route = respx.get(f"{BASE_URL}/listings")
        route.side_effect = [
            Response(
                200,
                json=_make_page_response(
                    page1,
                    count=230,
                    limit=LISTINGS_PAGE_SIZE,
                    skip=0,
                ),
            ),
            Response(
                200,
                json=_make_page_response(
                    page2,
                    count=230,
                    limit=LISTINGS_PAGE_SIZE,
                    skip=LISTINGS_PAGE_SIZE,
                ),
            ),
            Response(
                200,
                json=_make_page_response(
                    page3,
                    count=230,
                    limit=LISTINGS_PAGE_SIZE,
                    skip=LISTINGS_PAGE_SIZE * 2,
                ),
            ),
        ]

        client, _, _ = _make_client()
        result = await client.get_listings()
        assert len(result) == 230

        expected_fields = " ".join(LISTINGS_FIELDS)
        assert len(route.calls) == 3
        for idx, call in enumerate(route.calls):
            params = call.request.url.params
            assert params["limit"] == str(LISTINGS_PAGE_SIZE)
            assert params["skip"] == str(LISTINGS_PAGE_SIZE * idx)
            assert params["fields"] == expected_fields

    @respx.mock
    async def test_empty_account(self) -> None:
        """Empty account returns empty list."""
        from custom_components.guesty.api.const import (
            LISTINGS_FIELDS,
            LISTINGS_PAGE_SIZE,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        route = respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json=_make_page_response([], count=0),
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_listings()
        assert result == []

        assert len(route.calls) == 1
        params = route.calls[0].request.url.params
        assert params["limit"] == str(LISTINGS_PAGE_SIZE)
        assert params["skip"] == "0"
        assert params["fields"] == " ".join(LISTINGS_FIELDS)

    @respx.mock
    async def test_auth_error_propagation(self) -> None:
        """GuestyAuthError propagates from get_listings."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                401,
                json={"error": "invalid_client"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyAuthError):
            await client.get_listings()

    @respx.mock
    async def test_connection_error_propagation(self) -> None:
        """GuestyConnectionError propagates from get_listings."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.ConnectError("refused"),
        )
        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyConnectionError),
        ):
            await client.get_listings()

    @respx.mock
    async def test_response_error_on_malformed(self) -> None:
        """GuestyResponseError on malformed API response."""
        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json={"unexpected": "format"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyResponseError):
            await client.get_listings()

    @respx.mock
    async def test_non_success_status_raises(self) -> None:
        """Non-success HTTP status raises GuestyResponseError."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(422, text="Unprocessable Entity"),
        )
        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyResponseError,
                match="Listings fetch failed: status 422",
            ),
        ):
            await client.get_listings()

    @respx.mock
    async def test_invalid_json_raises(self) -> None:
        """Non-JSON response body raises GuestyResponseError."""
        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                content=b"not json",
                headers={"content-type": "text/plain"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.get_listings()

    @respx.mock
    async def test_non_dict_json_raises(self) -> None:
        """Non-dict JSON body raises GuestyResponseError."""
        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(200, json=[1, 2, 3]),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a JSON object",
        ):
            await client.get_listings()

    @respx.mock
    async def test_non_list_results_raises(self) -> None:
        """Non-list results field raises GuestyResponseError."""
        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json={"results": "not-a-list", "count": 0},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a list",
        ):
            await client.get_listings()

    @respx.mock
    async def test_debug_logs_sample_keys(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Debug log emits sorted keys of first listing."""
        import logging

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings = [_make_listing_dict()]
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json=_make_page_response(
                    listings,
                    count=1,
                ),
            ),
        )
        client, _, _ = _make_client()
        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.api.client",
        ):
            result = await client.get_listings()
        assert len(result) == 1
        expected_keys = sorted(listings[0].keys())
        assert f"Sample listing API keys: {expected_keys}" in caplog.text


# ── get_reservations() Tests (T004) ─────────────────────────────────


def _make_reservation_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API reservation dictionary with defaults.

    Args:
        **overrides: Fields to override on the default reservation.

    Returns:
        Dictionary matching the Guesty API reservation format.
    """
    defaults: dict[str, Any] = {
        "_id": "res-001",
        "listingId": "listing-001",
        "status": "confirmed",
        "checkIn": "2025-08-17T15:00:00.000Z",
        "checkOut": "2025-08-22T11:00:00.000Z",
        "confirmationCode": "GY-h5SdcsBL",
        "checkInDateLocalized": "2025-08-17",
        "checkOutDateLocalized": "2025-08-22",
        "plannedArrival": "16:00",
        "plannedDeparture": "10:00",
        "nightsCount": 5,
        "guestsCount": 3,
        "guest": {
            "fullName": "Jane Smith",
            "phone": "+1-555-0123",
            "email": "jane@example.com",
        },
        "money": {
            "totalPaid": 1250.00,
            "balanceDue": 0.00,
            "currency": "USD",
        },
        "source": "airbnb",
        "note": "Late check-in",
    }
    defaults.update(overrides)
    return defaults


def _make_reservations_page(
    reservations: list[dict[str, Any]],
    *,
    count: int = 1,
    limit: int = 100,
    skip: int = 0,
) -> dict[str, Any]:
    """Create a Guesty reservations page response.

    Args:
        reservations: Array of reservation dictionaries.
        count: Total count metadata.
        limit: Page size.
        skip: Offset.

    Returns:
        Dictionary matching the reservations endpoint format.
    """
    return {
        "results": reservations,
        "count": count,
        "limit": limit,
        "skip": skip,
    }


def _mock_token_endpoint() -> None:
    """Mock the token endpoint for all reservation tests."""
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json=make_token_response()),
    )


class TestGetReservations:
    """Tests for GuestyApiClient.get_reservations()."""

    @respx.mock
    async def test_single_page_fetch(self) -> None:
        """Single page with fewer results than limit stops."""
        from custom_components.guesty.api.const import (
            RESERVATIONS_FIELDS,
            RESERVATIONS_PAGE_SIZE,
        )

        _mock_token_endpoint()
        reservations = [_make_reservation_dict(_id=f"res-{i}") for i in range(5)]
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            # Primary request
            Response(
                200,
                json=_make_reservations_page(
                    reservations,
                    count=5,
                ),
            ),
            # Secondary checked_in request
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        result = (await client.get_reservations()).reservations
        assert len(result) == 5
        assert result[0].id == "res-0"

        # Verify primary request params
        primary_req = route.calls[0].request
        params = primary_req.url.params
        assert params["limit"] == str(RESERVATIONS_PAGE_SIZE)
        assert params["skip"] == "0"
        assert params["sort"] == "_id"
        assert params["fields"] == " ".join(RESERVATIONS_FIELDS)
        # Verify primary request has query params
        assert "checkIn" in params
        assert "checkOut" in params
        assert "status" in params
        assert "filters" not in params

    @respx.mock
    async def test_multi_page_pagination(self) -> None:
        """Paginate through multiple pages."""
        from custom_components.guesty.api.const import (
            RESERVATIONS_PAGE_SIZE,
        )

        _mock_token_endpoint()
        page1 = [
            _make_reservation_dict(_id=f"p1-{i}") for i in range(RESERVATIONS_PAGE_SIZE)
        ]
        page2 = [_make_reservation_dict(_id=f"p2-{i}") for i in range(30)]
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            # Primary: page 1 (full)
            Response(
                200,
                json=_make_reservations_page(
                    page1,
                    count=130,
                    limit=RESERVATIONS_PAGE_SIZE,
                    skip=0,
                ),
            ),
            # Primary: page 2 (partial)
            Response(
                200,
                json=_make_reservations_page(
                    page2,
                    count=130,
                    limit=RESERVATIONS_PAGE_SIZE,
                    skip=RESERVATIONS_PAGE_SIZE,
                ),
            ),
            # Secondary: empty
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        result = (await client.get_reservations()).reservations
        assert len(result) == 130

    @respx.mock
    async def test_empty_account_returns_empty_list(self) -> None:
        """Empty account returns empty list."""
        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        result = (await client.get_reservations()).reservations
        assert result == ()

    @respx.mock
    async def test_dual_request_merge(self) -> None:
        """Primary and secondary results are merged."""
        _mock_token_endpoint()
        primary = [_make_reservation_dict(_id="primary-1")]
        secondary = [
            _make_reservation_dict(
                _id="secondary-1",
                status="checked_in",
            ),
        ]
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(primary, count=1),
            ),
            Response(
                200,
                json=_make_reservations_page(
                    secondary,
                    count=1,
                ),
            ),
        ]
        client, _, _ = _make_client()
        result = (await client.get_reservations()).reservations
        assert len(result) == 2
        ids = {r.id for r in result}
        assert ids == {"primary-1", "secondary-1"}

    @respx.mock
    async def test_deduplication_by_reservation_id(self) -> None:
        """Duplicate IDs across requests are de-duplicated."""
        _mock_token_endpoint()
        shared = _make_reservation_dict(
            _id="shared-1",
            status="checked_in",
        )
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    [shared],
                    count=1,
                ),
            ),
            Response(
                200,
                json=_make_reservations_page(
                    [shared],
                    count=1,
                ),
            ),
        ]
        client, _, _ = _make_client()
        result = (await client.get_reservations()).reservations
        assert len(result) == 1
        assert result[0].id == "shared-1"

    @respx.mock
    async def test_secondary_checked_in_filter(self) -> None:
        """Secondary request uses checked_in status filter."""
        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        await client.get_reservations()
        # Second call is the secondary request
        secondary_req = route.calls[1].request
        assert secondary_req.url.params["status"] == "checked_in"
        assert "checkIn" not in secondary_req.url.params
        assert "checkOut" not in secondary_req.url.params
        assert "filters" not in secondary_req.url.params

    @respx.mock
    async def test_date_range_boundaries(self) -> None:
        """Date boundaries computed from past/future days."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.reservations_legacy import (
            datetime as client_datetime,
        )

        _mock_token_endpoint()
        fixed_now = datetime(2025, 8, 1, 12, 0, 0, tzinfo=UTC)
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        with _patch(
            "custom_components.guesty.api.reservations_legacy.datetime",
            wraps=client_datetime,
        ) as mock_dt:
            mock_dt.now.return_value = fixed_now
            await client.get_reservations(
                past_days=7,
                future_days=14,
            )

        primary_req = route.calls[0].request
        params = primary_req.url.params
        assert params["checkIn"] == "2025-07-25"
        assert params["checkOut"] == "2025-08-15"

    @respx.mock
    async def test_custom_statuses(self) -> None:
        """Custom statuses are passed through in filter."""
        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        custom = frozenset({"confirmed", "checked_in"})
        await client.get_reservations(statuses=custom)
        primary_req = route.calls[0].request
        status_param = primary_req.url.params["status"]
        assert sorted(status_param.split(",")) == [
            "checked_in",
            "confirmed",
        ]

    @respx.mock
    async def test_auth_error_propagation(self) -> None:
        """GuestyAuthError propagates from get_reservations."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                401,
                json={"error": "invalid_client"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyAuthError):
            await client.get_reservations()

    @respx.mock
    async def test_connection_error_propagation(self) -> None:
        """GuestyConnectionError propagates."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            side_effect=httpx.ConnectError("refused"),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyConnectionError):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_malformed_json(self) -> None:
        """GuestyResponseError on malformed API response."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(
                200,
                content=b"not json",
                headers={"content-type": "text/plain"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_non_dict(self) -> None:
        """GuestyResponseError on non-dict JSON response."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(200, json=[1, 2, 3]),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a JSON object",
        ):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_missing_results(self) -> None:
        """GuestyResponseError when results key is absent."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(
                200,
                json={"unexpected": "format"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyResponseError):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_non_list_results(self) -> None:
        """GuestyResponseError when results is not a list."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(
                200,
                json={"results": "not-a-list", "count": 0},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a list",
        ):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_non_success(self) -> None:
        """Non-success HTTP status raises GuestyResponseError."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(422, text="Unprocessable Entity"),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="Reservations fetch failed",
        ):
            await client.get_reservations()

    @respx.mock
    async def test_invalid_reservations_skipped(self) -> None:
        """Reservations with missing fields are skipped."""
        _mock_token_endpoint()
        valid = _make_reservation_dict(_id="valid-1")
        invalid = {"status": "confirmed"}  # missing _id
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    [valid, invalid],
                    count=2,
                ),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        result = (await client.get_reservations()).reservations
        assert len(result) == 1
        assert result[0].id == "valid-1"

    @respx.mock
    async def test_default_actionable_statuses(self) -> None:
        """Default statuses filter uses ACTIONABLE_STATUSES."""
        from custom_components.guesty.api.const import (
            ACTIONABLE_STATUSES,
        )

        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        await client.get_reservations()
        primary_req = route.calls[0].request
        status_param = primary_req.url.params["status"]
        assert set(status_param.split(",")) == ACTIONABLE_STATUSES

    @respx.mock
    async def test_debug_logs_reservation_sample_keys(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Debug log emits sorted keys of first reservation and guest."""
        import logging

        _mock_token_endpoint()
        reservations = [_make_reservation_dict()]
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    reservations,
                    count=1,
                ),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.api.reservations_legacy",
        ):
            result = (await client.get_reservations()).reservations
        assert len(result) == 1
        expected_keys = sorted(reservations[0].keys())
        expected_guest_keys = sorted(
            reservations[0]["guest"].keys(),
        )
        assert f"Sample reservation keys: {expected_keys}" in caplog.text
        assert f"guest keys: {expected_guest_keys}" in caplog.text


# ── Phase 4: Transient Failure Retry Tests (T025) ───────────────────


class TestTransientRetry:
    """Tests for transient failure retry with backoff (T025)."""

    @respx.mock
    async def test_connect_error_retry_then_success(self) -> None:
        """ConnectError retried then succeeds on next attempt."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        call_count = 0

        async def _side_effect(
            request: httpx.Request,
        ) -> Response:
            """Fail first, succeed second."""
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ConnectError("connection refused")
            return Response(200, json={"results": []})

        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=_side_effect,
        )

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True
        assert call_count == 2

    @respx.mock
    async def test_timeout_retry_then_success(self) -> None:
        """TimeoutException retried then succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        call_count = 0

        async def _side_effect(
            request: httpx.Request,
        ) -> Response:
            """Timeout first, succeed second."""
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.TimeoutException("timed out")
            return Response(200, json={"results": []})

        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=_side_effect,
        )

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True

    @respx.mock
    async def test_persistent_connect_error_exhausts_retries(
        self,
    ) -> None:
        """Persistent ConnectError raises after retry exhaustion."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.ConnectError("refused"),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyConnectionError,
                match="after 3 retries",
            ),
        ):
            await client.test_connection()

    @respx.mock
    async def test_connect_error_uses_exponential_backoff(
        self,
    ) -> None:
        """Connect error retries use exponential backoff."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        call_count = 0

        async def _side_effect(
            request: httpx.Request,
        ) -> Response:
            """Fail twice, succeed third."""
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.ConnectError("refused")
            return Response(200, json={"results": []})

        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=_side_effect,
        )

        mock_sleep = AsyncMock()
        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            await client.test_connection()

        # Two retries means two sleep calls
        assert mock_sleep.call_count == 2
        delays = [c[0][0] for c in mock_sleep.call_args_list]
        # Second delay should be larger (exponential backoff)
        assert delays[1] > delays[0]

    @respx.mock
    async def test_connect_error_logged_at_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Transient connect error retries logged at warning."""
        import logging
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        call_count = 0

        async def _side_effect(
            request: httpx.Request,
        ) -> Response:
            """Fail first, succeed second."""
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ConnectError("connection refused")
            return Response(200, json={"results": []})

        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=_side_effect,
        )

        client, _, _ = _make_client()
        with (
            caplog.at_level(logging.WARNING),
            _patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client.test_connection()

        assert any(
            "Transient error" in record.getMessage()
            and record.levelno == logging.WARNING
            for record in caplog.records
        )

    @respx.mock
    async def test_5xx_retry_then_success(self) -> None:
        """5xx response retried then succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(502, text="Bad Gateway"),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True

    @respx.mock
    async def test_503_retry_then_success(self) -> None:
        """503 Service Unavailable retried then succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(503, text="Service Unavailable"),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True

    @respx.mock
    async def test_504_retry_then_success(self) -> None:
        """504 Gateway Timeout retried then succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(504, text="Gateway Timeout"),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True

    @respx.mock
    async def test_500_exhausts_retries_raises_error(
        self,
    ) -> None:
        """Persistent 500 raises GuestyConnectionError after retries."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyConnectionError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                500,
                text="Internal Server Error",
            ),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyConnectionError,
                match="after 3 retries",
            ),
        ):
            await client.test_connection()

    @respx.mock
    async def test_5xx_logged_at_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """5xx retry logged at warning with status code."""
        import logging
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(503, text="Service Unavailable"),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with (
            caplog.at_level(logging.WARNING),
            _patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client.test_connection()

        assert any(
            "Server error 503" in record.getMessage()
            and record.levelno == logging.WARNING
            for record in caplog.records
        )

    @respx.mock
    async def test_429_retry_preserves_retry_after(self) -> None:
        """429 handling preserves Retry-After header."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "3"},
            ),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            await client.test_connection()

        mock_sleep.assert_called_once()
        assert mock_sleep.call_args[0][0] == 3.0

    @respx.mock
    async def test_non_retryable_status_not_retried(self) -> None:
        """Non-transient status (e.g. 422) is not retried."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                422,
                json={"error": "Unprocessable"},
            ),
        )

        client, _, _ = _make_client()
        # 422 is not retried — returned directly
        response = await client._request("GET", "/listings")
        assert response.status_code == 422


class TestGetAccountId:
    """Tests for GuestyApiClient.get_account_id()."""

    @respx.mock
    async def test_successful_account_id(self) -> None:
        """get_account_id() returns account ID on success."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": "acc-123-abc"},
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_account_id()
        assert result == "acc-123-abc"

    @respx.mock
    async def test_non_success_raises_error(self) -> None:
        """get_account_id() raises on non-2xx status."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                404,
                json={"error": "not found"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="Account lookup failed",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_invalid_json_raises_error(self) -> None:
        """get_account_id() raises on non-JSON response."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                content=b"not json",
                headers={"content-type": "text/plain"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_missing_id_field_raises_error(self) -> None:
        """get_account_id() raises when _id is missing."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"name": "My Account"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="missing _id",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_empty_id_field_raises_error(self) -> None:
        """get_account_id() raises when _id is empty."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": ""},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="missing _id",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_whitespace_id_raises_error(self) -> None:
        """get_account_id() raises when _id is whitespace-only."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": "   "},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="missing _id",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_strips_whitespace_from_id(self) -> None:
        """get_account_id() strips whitespace from _id."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": "  acc-trimmed  "},
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_account_id()
        assert result == "acc-trimmed"

    @respx.mock
    async def test_non_dict_response_raises_error(self) -> None:
        """get_account_id() raises when response is not a dict."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json=["not", "a", "dict"],
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="JSON object",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_non_string_id_raises_error(self) -> None:
        """get_account_id() raises when _id is not a string."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": 12345},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="missing _id",
        ):
            await client.get_account_id()


# ── Reservation source seam (T007, T013) ────────────────────────────


class TestReservationsApiResolution:
    """Tests for resolve_reservations_api()."""

    def test_legacy_resolves_to_legacy(self) -> None:
        """The literal 'legacy' is preserved."""
        assert resolve_reservations_api("legacy") == RESERVATIONS_API_LEGACY

    def test_v3_resolves_to_v3(self) -> None:
        """The literal 'v3' is preserved."""
        assert resolve_reservations_api("v3") == RESERVATIONS_API_V3

    def test_unknown_value_resolves_to_v3_with_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An unparsable stored value resolves to v3 with a WARNING."""
        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.api.client",
        ):
            assert resolve_reservations_api("nonsense") == RESERVATIONS_API_V3
        assert "nonsense" in caplog.text
        assert "v3" in caplog.text

    def test_non_string_resolves_to_v3_with_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-string stored value resolves to v3 with a WARNING."""
        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.api.client",
        ):
            assert resolve_reservations_api(17) == RESERVATIONS_API_V3
        assert caplog.records

    def test_client_exposes_resolved_value(self) -> None:
        """The client exposes the resolved reservations_api value."""
        http = httpx.AsyncClient()
        token_manager = GuestyTokenManager(
            client_id=FAKE_CLIENT_ID,
            client_secret=FAKE_CLIENT_SECRET,
            http_client=http,
            storage=FakeTokenStorage(),
            refresh_buffer=0,
        )
        client = GuestyApiClient(
            token_manager=token_manager,
            http_client=http,
            reservations_api="nonsense",
        )
        assert client.reservations_api == RESERVATIONS_API_V3

    def test_client_defaults_to_legacy(self) -> None:
        """The client default matches the staged legacy default."""
        client, _, _ = _make_client()
        assert client.reservations_api == RESERVATIONS_API_LEGACY


class TestReservationSourceDelegation:
    """Tests for get_reservations() source delegation (T007)."""

    async def test_delegates_to_legacy_source(self) -> None:
        """A legacy client dispatches to the legacy source."""
        client, _, _ = _make_client(reservations_api=RESERVATIONS_API_LEGACY)
        reservation = GuestyReservation.from_api_dict(
            _make_reservation_dict(),
        )
        assert reservation is not None
        fetch = AsyncMock(
            return_value=ReservationFetchResult(
                reservations=(reservation,),
                pages=2,
            ),
        )
        with patch.object(LegacyReservationSource, "fetch", fetch):
            result = await client.get_reservations(
                past_days=7,
                future_days=14,
            )

        assert result.reservations == (reservation,)
        assert result.pages == 2
        fetch.assert_awaited_once_with(
            past_days=7,
            future_days=14,
            statuses=None,
            candidates=None,
        )

    @respx.mock
    async def test_legacy_source_issues_two_sequences(self) -> None:
        """The legacy source issues the two historical requests."""
        from custom_components.guesty.api.const import (
            RESERVATIONS_FIELDS,
            RESERVATIONS_PAGE_SIZE,
        )

        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    [_make_reservation_dict(_id="res-0")],
                ),
            ),
            Response(200, json=_make_reservations_page([])),
        ]
        client, _, _ = _make_client(reservations_api=RESERVATIONS_API_LEGACY)
        source = LegacyReservationSource(client)
        result = await source.fetch(
            past_days=30,
            future_days=365,
            statuses=None,
        )

        assert len(route.calls) == 2
        primary = route.calls[0].request.url.params
        assert primary["limit"] == str(RESERVATIONS_PAGE_SIZE)
        assert primary["sort"] == "_id"
        assert primary["fields"] == " ".join(RESERVATIONS_FIELDS)
        assert "checkIn" in primary
        assert "checkOut" in primary
        secondary = route.calls[1].request.url.params
        assert secondary["status"] == "checked_in"
        assert "checkIn" not in secondary
        assert len(result.reservations) == 1

    @respx.mock
    async def test_legacy_source_returns_fetch_result(self) -> None:
        """The legacy source populates the counters it can (T009)."""
        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    [
                        _make_reservation_dict(_id="res-0"),
                        _make_reservation_dict(
                            _id="res-1",
                            status="checked_in",
                        ),
                    ],
                ),
            ),
            Response(200, json=_make_reservations_page([])),
        ]
        client, _, _ = _make_client(reservations_api=RESERVATIONS_API_LEGACY)
        source = LegacyReservationSource(client)
        result = await source.fetch(
            past_days=30,
            future_days=365,
            statuses=None,
        )

        assert isinstance(result, ReservationFetchResult)
        assert result.pages == 2
        assert dict(result.status_counts) == {
            "confirmed": 1,
            "checked_in": 1,
        }
        assert dict(result.segment_histogram) == {}

    @respx.mock
    async def test_legacy_source_counters_are_best_effort(self) -> None:
        """A structured status is served but left uncounted."""
        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    [
                        _make_reservation_dict(_id="res-0"),
                        _make_reservation_dict(
                            _id="res-1",
                            status={"value": "checked_in"},
                        ),
                    ],
                ),
            ),
            Response(200, json=_make_reservations_page([])),
        ]
        client, _, _ = _make_client(reservations_api=RESERVATIONS_API_LEGACY)
        source = LegacyReservationSource(client)
        result = await source.fetch(
            past_days=30,
            future_days=365,
            statuses=None,
        )

        assert len(result.reservations) == 2
        assert dict(result.status_counts) == {"confirmed": 1}
