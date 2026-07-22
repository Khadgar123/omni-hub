from __future__ import annotations

import email.message
import errno
import io
import os
from pathlib import Path
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import omni_hub.connectors.discord as discord_api_module
from omni_hub.connectors.discord import (
    DiscordAPIError,
    DiscordHTTPTransport,
    DiscordMediaResolutionError,
    DiscordMediaResolutionInvalidAnswer,
    DiscordMediaSecurityError,
    iter_joined_private_archived_thread_pages,
    iter_message_pages,
    iter_pin_pages,
    iter_private_archived_thread_pages,
    iter_public_archived_thread_pages,
    read_bot_token,
)


def _resolver_for(*addresses: str):  # type: ignore[no-untyped-def]
    def resolve(
        _host: str,
        port: int,
        *_args: object,
        **_kwargs: object,
    ) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
        rows = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr: tuple[object, ...] = (
                (address, port, 0, 0)
                if family == socket.AF_INET6
                else (address, port)
            )
            rows.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return rows

    return resolve


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        chunks: list[bytes] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = io.BytesIO(body)
        self._chunks = iter(chunks) if chunks is not None else None
        self.headers = headers or {}
        self.closed = False

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def read(self, size: int = -1) -> bytes:
        if self._chunks is None:
            return self._body.read(size)
        return next(self._chunks, b"")


def _http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://discord.com/api/v10/test",
        status,
        "upstream error",
        email.message.Message(),
        io.BytesIO(body),
    )


class DiscordCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _token_file(self, mode: int = 0o600, content: str = "unit-test-secret\n") -> Path:
        path = self.directory / "bot-token"
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)
        return path

    def test_reads_owner_only_regular_token_file(self) -> None:
        self.assertEqual(read_bot_token(self._token_file()), "unit-test-secret")

    def test_rejects_symbolic_link(self) -> None:
        target = self._token_file()
        link = self.directory / "token-link"
        link.symlink_to(target)

        with self.assertRaises(ValueError):
            read_bot_token(link)

    def test_rejects_group_or_other_permissions(self) -> None:
        for mode in (0o640, 0o604):
            with self.subTest(mode=oct(mode)):
                path = self._token_file(mode)
                with self.assertRaises(ValueError):
                    read_bot_token(path)

    def test_rejects_empty_token(self) -> None:
        with self.assertRaises(ValueError):
            read_bot_token(self._token_file(content="  \n"))


class DiscordHTTPTransportTests(unittest.TestCase):
    token = "unit-test-secret"

    def test_authorization_is_redacted_from_repr_and_errors(self) -> None:
        def rejecting_opener(request: object, *, timeout: float) -> object:
            del timeout
            authorization = dict(request.header_items())["Authorization"]  # type: ignore[attr-defined]
            raise RuntimeError(f"rejected {authorization}")

        transport = DiscordHTTPTransport(self.token, opener=rejecting_opener)

        self.assertNotIn(self.token, repr(transport))
        with self.assertRaises(DiscordAPIError) as caught:
            transport.get_json("/users/@me")
        self.assertNotIn(self.token, str(caught.exception))
        self.assertNotIn(self.token, repr(caught.exception))

    def test_json_requests_reject_absolute_urls_before_sending_token(self) -> None:
        calls = 0

        def opener(_request: object, *, timeout: float) -> _Response:
            nonlocal calls
            del timeout
            calls += 1
            return _Response(b"{}")

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=_resolver_for("93.184.216.34"),
        )

        with self.assertRaises(ValueError):
            transport.get_json("https://example.com/credential-sink")
        self.assertEqual(calls, 0)

    def test_api_base_url_must_be_credential_free_https(self) -> None:
        invalid_urls = (
            "http://discord.com/api/v10",
            "https://user:password@discord.com/api/v10",
            "https://discord.com/api/v10?token=secret",
            "https://discord.com/api/v10#fragment",
        )
        for base_url in invalid_urls:
            with self.subTest(base_url=base_url):
                with self.assertRaisesRegex(ValueError, "credential-free HTTPS"):
                    DiscordHTTPTransport(self.token, base_url=base_url)

    def test_default_transport_disables_authorized_redirects(self) -> None:
        api_opener = Mock()
        media_opener = Mock()
        with patch(
            "omni_hub.connectors.discord.urllib.request.build_opener",
            side_effect=(api_opener, media_opener),
        ) as build_opener:
            transport = DiscordHTTPTransport(self.token)

        redirect_handler = build_opener.call_args_list[0].args[0]
        original = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {self.token}"},
        )
        redirected = redirect_handler.redirect_request(
            original,
            None,
            302,
            "Found",
            {},
            "https://credential-sink.example/",
        )

        self.assertIsNone(redirected)
        media_handlers = build_opener.call_args_list[1].args
        self.assertTrue(
            any(
                isinstance(handler, discord_api_module._PublicHTTPSHandler)
                for handler in media_handlers
            )
        )
        self.assertTrue(
            any(
                isinstance(
                    handler,
                    discord_api_module._PublicMediaRedirectHandler,
                )
                for handler in media_handlers
            )
        )
        self.assertIs(transport._api_opener, api_opener.open)
        self.assertIs(transport._media_opener, media_opener.open)

    def test_media_rejects_direct_private_link_local_and_metadata_addresses(
        self,
    ) -> None:
        calls = 0

        def opener(_request: object, *, timeout: float) -> _Response:
            nonlocal calls
            del timeout
            calls += 1
            return _Response(b"unsafe")

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=_resolver_for("93.184.216.34"),
        )
        private_urls = (
            "https://127.0.0.1/loopback",
            "https://[::1]/loopback",
            "https://10.1.2.3/private",
            "https://172.16.0.1/private",
            "https://192.168.1.1/private",
            "https://169.254.169.254/latest/meta-data/",
            "https://100.64.0.1/shared-address-space",
        )
        for url in private_urls:
            with self.subTest(url=url):
                with self.assertRaises(DiscordMediaSecurityError):
                    transport.open_byte_stream(url)
        with self.assertRaises(DiscordAPIError) as credential_error:
            transport.open_byte_stream(
                "https://user:media-secret@cdn.example/credentialed"
            )
        self.assertNotIn("media-secret", repr(credential_error.exception))
        self.assertEqual(calls, 0)

    def test_media_rejects_private_dns_and_redirect_targets(self) -> None:
        resolver = _resolver_for("10.0.0.7")
        calls = 0

        def opener(_request: object, *, timeout: float) -> _Response:
            nonlocal calls
            del timeout
            calls += 1
            return _Response(b"unsafe")

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=resolver,
        )
        with self.assertRaises(DiscordMediaSecurityError):
            transport.open_byte_stream("https://internal.example/secret")
        self.assertEqual(calls, 0)

        redirect_handler = discord_api_module._PublicMediaRedirectHandler(
            resolver=resolver
        )
        original = urllib.request.Request("https://cdn.discordapp.com/file")
        with self.assertRaises(DiscordMediaSecurityError):
            redirect_handler.redirect_request(
                original,
                None,
                302,
                "Found",
                {},
                "https://internal.example/redirected",
            )

    def test_rfc2544_fake_ip_requires_explicit_media_opt_in(self) -> None:
        requests: list[object] = []

        def opener(request: object, *, timeout: float) -> _Response:
            del timeout
            requests.append(request)
            return _Response(b"public")

        resolver = _resolver_for("198.18.0.222")
        default_transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=resolver,
        )
        with self.assertRaises(DiscordMediaSecurityError):
            default_transport.open_byte_stream("https://cdn.discordapp.com/file")
        self.assertEqual(requests, [])

        opted_in_transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=resolver,
            allow_rfc2544_fake_ip=True,
        )
        with opted_in_transport.open_byte_stream(
            "https://cdn.discordapp.com/file"
        ) as stream:
            self.assertEqual(list(stream), [b"public"])
        self.assertEqual(len(requests), 1)

    def test_rfc2544_opt_in_rejects_literals_and_other_private_answers(self) -> None:
        calls = 0

        def opener(_request: object, *, timeout: float) -> _Response:
            nonlocal calls
            del timeout
            calls += 1
            return _Response(b"unsafe")

        def resolver(
            host: str,
            port: int,
            *args: object,
            **kwargs: object,
        ):  # type: ignore[no-untyped-def]
            if host == "cdn.discordapp.com":
                return _resolver_for("198.18.0.222", "10.0.0.7")(
                    host, port, *args, **kwargs
                )
            address = "10.0.0.7" if host == "private.example" else "198.18.0.222"
            return _resolver_for(address)(host, port, *args, **kwargs)

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=resolver,
            allow_rfc2544_fake_ip=True,
        )
        unsafe_urls = (
            "https://198.18.0.1/file",
            "https://3323068417/file",
            "https://0xc6120001/file",
            "https://private.example/file",
            "https://169.254.169.254/latest/meta-data/",
            "https://unlisted.example/file",
            "https://cdn.discordapp.com.evil.example/file",
            "https://cdn.discordapp.com../file",
            "https://cdn.discоrdapp.com/file",
            "https://user:password@cdn.discordapp.com/file",
            "https://cdn.discordapp.com/file",
        )
        for url in unsafe_urls:
            with self.subTest(url=url):
                with self.assertRaises(DiscordMediaSecurityError):
                    transport.open_byte_stream(url)
        self.assertEqual(calls, 0)

    def test_rfc2544_allowlist_normalizes_case_and_trailing_dot(self) -> None:
        requests: list[object] = []

        def opener(request: object, *, timeout: float) -> _Response:
            del timeout
            requests.append(request)
            return _Response(b"public")

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=_resolver_for("198.18.0.222"),
            allow_rfc2544_fake_ip=True,
        )
        for url in (
            "https://CDN.DISCORDAPP.COM/file",
            "https://cdn.discordapp.com./file",
        ):
            with self.subTest(url=url):
                with transport.open_byte_stream(url) as stream:
                    self.assertEqual(list(stream), [b"public"])
        self.assertEqual(len(requests), 2)

    def test_rfc2544_opt_in_rejects_nonstandard_port_without_changing_public_literal_baseline(
        self,
    ) -> None:
        calls = 0

        def opener(_request: object, *, timeout: float) -> _Response:
            nonlocal calls
            del timeout
            calls += 1
            return _Response(b"unsafe")

        fake_ip_transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=_resolver_for("198.18.0.222"),
            allow_rfc2544_fake_ip=True,
        )
        with self.assertRaises(DiscordMediaSecurityError):
            fake_ip_transport.open_byte_stream(
                "https://cdn.discordapp.com:8443/file"
            )

        public_literal_transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=_resolver_for("8.8.8.8"),
            allow_rfc2544_fake_ip=True,
        )
        with public_literal_transport.open_byte_stream(
            "https://8.8.8.8/file"
        ) as stream:
            self.assertEqual(list(stream), [b"unsafe"])
        self.assertEqual(calls, 1)

    def test_rfc2544_redirects_are_revalidated_under_same_policy(self) -> None:
        def resolver(
            host: str,
            port: int,
            *args: object,
            **kwargs: object,
        ):  # type: ignore[no-untyped-def]
            address = "10.0.0.7" if host == "private.example" else "198.18.0.222"
            return _resolver_for(address)(host, port, *args, **kwargs)

        redirect_handler = discord_api_module._PublicMediaRedirectHandler(
            resolver=resolver,
            allow_rfc2544_fake_ip=True,
        )
        original = urllib.request.Request("https://cdn.example/file")
        redirected = redirect_handler.redirect_request(
            original,
            None,
            302,
            "Found",
            {},
            "https://media.discordapp.net/redirected",
        )
        self.assertIsNotNone(redirected)
        with self.assertRaises(DiscordMediaSecurityError):
            redirect_handler.redirect_request(
                original,
                None,
                302,
                "Found",
                {},
                "https://private.example/redirected",
            )
        with self.assertRaises(DiscordMediaSecurityError):
            redirect_handler.redirect_request(
                original,
                None,
                302,
                "Found",
                {},
                "https://cdn.discordapp.com.evil.example/redirected",
            )

    def test_rfc2544_connect_gate_accepts_only_allowlisted_dns_on_443(self) -> None:
        class SocketFixture:
            def __init__(self) -> None:
                self.connected: list[tuple[object, ...]] = []

            def settimeout(self, _timeout: object) -> None:
                return None

            def bind(self, _source: object) -> None:
                return None

            def connect(self, address: tuple[object, ...]) -> None:
                self.connected.append(address)

            def close(self) -> None:
                return None

        socket_fixture = SocketFixture()
        connected = discord_api_module._create_public_connection(
            ("cdn.discordapp.com", 443),
            timeout=1.0,
            source_address=None,
            resolver=_resolver_for("198.18.0.222"),
            socket_factory=lambda *_args: socket_fixture,
            allow_rfc2544_fake_ip=True,
        )
        self.assertIs(connected, socket_fixture)
        self.assertEqual(socket_fixture.connected, [("198.18.0.222", 443)])

        unsafe_cases = (
            ("unlisted.example", 443, _resolver_for("198.18.0.222")),
            ("cdn.discordapp.com", 8443, _resolver_for("198.18.0.222")),
            (
                "cdn.discordapp.com",
                443,
                _resolver_for("198.18.0.222", "10.0.0.7"),
            ),
        )
        for host, port, resolver in unsafe_cases:
            socket_calls = 0

            def socket_factory(*_args: object) -> SocketFixture:
                nonlocal socket_calls
                socket_calls += 1
                return SocketFixture()

            with self.subTest(host=host, port=port):
                with self.assertRaises(DiscordMediaSecurityError):
                    discord_api_module._create_public_connection(
                        (host, port),
                        timeout=1.0,
                        source_address=None,
                        resolver=resolver,
                        socket_factory=socket_factory,
                        allow_rfc2544_fake_ip=True,
                    )
                self.assertEqual(socket_calls, 0)

    def test_public_https_handler_threads_fake_ip_policy_to_connection(self) -> None:
        resolver = _resolver_for("198.18.0.222")
        handler = discord_api_module._PublicHTTPSHandler(
            resolver=resolver,
            allow_rfc2544_fake_ip=True,
        )
        handler.do_open = Mock(return_value=_Response(b"ok"))  # type: ignore[method-assign]

        handler.https_open(urllib.request.Request("https://cdn.discordapp.com/file"))

        connection_factory = handler.do_open.call_args.args[0]
        self.assertIs(
            connection_factory.keywords["resolver"],
            resolver,
        )
        self.assertIs(
            connection_factory.keywords["allow_rfc2544_fake_ip"],
            True,
        )

    def test_media_connect_revalidates_and_pins_dns_answer(self) -> None:
        answers = iter(
            (
                _resolver_for("93.184.216.34"),
                _resolver_for("127.0.0.1"),
            )
        )

        def rebinding_resolver(
            host: str,
            port: int,
            *args: object,
            **kwargs: object,
        ):  # type: ignore[no-untyped-def]
            return next(answers)(host, port, *args, **kwargs)

        discord_api_module._validate_public_media_url(
            "https://cdn.example/file",
            resolver=rebinding_resolver,
        )
        sockets: list[object] = []

        def socket_factory(*_args: object) -> object:
            socket_object = object()
            sockets.append(socket_object)
            return socket_object

        with self.assertRaises(DiscordMediaSecurityError):
            discord_api_module._create_public_connection(
                ("cdn.example", 443),
                timeout=1.0,
                source_address=None,
                resolver=rebinding_resolver,
                socket_factory=socket_factory,
            )
        self.assertEqual(sockets, [])

    def test_media_resolution_transient_errors_are_typed_without_opening(self) -> None:
        cases = (
            (socket.gaierror(socket.EAI_AGAIN, "again"), "resolver_eai_again"),
            (TimeoutError(), "resolver_timeout"),
            (OSError(errno.ETIMEDOUT, "timeout"), "resolver_timeout"),
        )
        for resolver_error, expected in cases:
            opener = Mock()
            transport = DiscordHTTPTransport(
                self.token,
                opener=opener,
                resolver=Mock(side_effect=resolver_error),
            )
            with self.subTest(expected=expected):
                with self.assertRaises(DiscordMediaResolutionError) as caught:
                    transport.open_byte_stream("https://cdn.example/file")
                self.assertEqual(caught.exception.reason_code, expected)
                opener.assert_not_called()

    def test_media_resolution_name_errors_are_typed_without_opening(self) -> None:
        cases: list[tuple[OSError, str]] = [
            (
                socket.gaierror(socket.EAI_NONAME, "missing"),
                "resolver_name_not_found",
            ),
        ]
        eai_nodata = getattr(socket, "EAI_NODATA", None)
        if eai_nodata is not None and eai_nodata != socket.EAI_NONAME:
            cases.append(
                (socket.gaierror(eai_nodata, "no data"), "resolver_no_data")
            )
        for resolver_error, expected in cases:
            opener = Mock()
            transport = DiscordHTTPTransport(
                self.token,
                opener=opener,
                resolver=Mock(side_effect=resolver_error),
            )
            with self.subTest(expected=expected):
                with self.assertRaises(DiscordMediaResolutionError) as caught:
                    transport.open_byte_stream("https://cdn.example/file")
                self.assertEqual(caught.exception.reason_code, expected)
                opener.assert_not_called()

    def test_media_resolution_invalid_answers_are_typed_without_opening(self) -> None:
        malformed_answers: tuple[tuple[object, type[DiscordAPIError], str], ...] = (
            ([], DiscordMediaResolutionError, "resolver_empty_answer"),
            ((), DiscordMediaResolutionError, "resolver_empty_answer"),
            (
                {"not": "a resolver answer"},
                DiscordMediaResolutionInvalidAnswer,
                "resolver_invalid_answer",
            ),
            (
                [(socket.AF_UNIX, socket.SOCK_STREAM, 0, "", ("path",))],
                DiscordMediaResolutionInvalidAnswer,
                "resolver_invalid_answer",
            ),
            (
                [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ())],
                DiscordMediaResolutionInvalidAnswer,
                "resolver_invalid_answer",
            ),
            (
                [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("not-an-ip", 443))],
                DiscordMediaResolutionInvalidAnswer,
                "resolver_invalid_answer",
            ),
        )
        for raw_answers, expected_type, expected_reason in malformed_answers:
            opener = Mock()
            transport = DiscordHTTPTransport(
                self.token,
                opener=opener,
                resolver=Mock(return_value=raw_answers),
            )
            with self.subTest(raw_answers=raw_answers):
                with self.assertRaises(expected_type) as caught:
                    transport.open_byte_stream("https://cdn.example/file")
                self.assertEqual(caught.exception.reason_code, expected_reason)
                opener.assert_not_called()

    def test_media_resolution_unclassified_os_error_is_typed_without_opening(self) -> None:
        opener = Mock()
        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=Mock(side_effect=OSError(errno.ECONNREFUSED, "refused")),
        )
        with self.assertRaises(DiscordMediaResolutionError) as caught:
            transport.open_byte_stream("https://cdn.example/file")
        self.assertEqual(caught.exception.reason_code, "resolver_os_error_unclassified")
        opener.assert_not_called()

    def test_media_connection_resolver_eai_again_is_typed_before_socket_creation(
        self,
    ) -> None:
        socket_factory = Mock()
        with self.assertRaises(DiscordMediaResolutionError) as connect_error:
            discord_api_module._create_public_connection(
                ("cdn.example", 443),
                resolver=Mock(
                    side_effect=socket.gaierror(socket.EAI_AGAIN, "again")
                ),
                socket_factory=socket_factory,
            )
        self.assertEqual(connect_error.exception.reason_code, "resolver_eai_again")
        socket_factory.assert_not_called()

    def test_media_resolution_invalid_answer_prevents_socket_creation(self) -> None:
        socket_factory = Mock()
        with self.assertRaises(DiscordMediaResolutionInvalidAnswer) as caught:
            discord_api_module._create_public_connection(
                ("cdn.example", 443),
                resolver=Mock(return_value=[("bad",)]),
                socket_factory=socket_factory,
            )
        self.assertEqual(caught.exception.reason_code, "resolver_invalid_answer")
        socket_factory.assert_not_called()

    def test_media_connection_rejects_unbound_resolver_sockaddr_before_socket(self) -> None:
        malformed_answers = (
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 8443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", True)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 65536)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443, 0, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", 443, True, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", 443, 1 << 20, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700:4700::1111", 443, 0, True)),
            (socket.AF_INET, True, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, True, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_UDP, "", ("93.184.216.34", 443)),
        )
        for answer in malformed_answers:
            with self.subTest(answer=answer):
                socket_factory = Mock()
                with self.assertRaises(DiscordMediaResolutionInvalidAnswer):
                    discord_api_module._create_public_connection(
                        ("cdn.example", 443),
                        resolver=Mock(return_value=[answer]),
                        socket_factory=socket_factory,
                    )
                socket_factory.assert_not_called()

    def test_media_connection_rejects_multicast_answers_before_socket(self) -> None:
        public_answer = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )
        multicast_answers = (
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("224.0.0.1", 443),
            ),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("ff02::1", 443, 0, 0),
            ),
        )
        for answers in (
            [multicast_answers[0]],
            [multicast_answers[1]],
            [public_answer, multicast_answers[0]],
        ):
            with self.subTest(answers=answers):
                socket_factory = Mock()
                with self.assertRaises(DiscordMediaSecurityError):
                    discord_api_module._create_public_connection(
                        ("cdn.example", 443),
                        resolver=Mock(return_value=answers),
                        socket_factory=socket_factory,
                    )
                socket_factory.assert_not_called()

        literal_resolver = Mock()
        literal_error: DiscordAPIError | None = None
        try:
            discord_api_module._create_public_connection(
                ("224.0.0.1", 443),
                resolver=literal_resolver,
                socket_factory=Mock(),
            )
        except DiscordAPIError as exc:
            literal_error = exc
        self.assertIsInstance(literal_error, DiscordMediaSecurityError)
        literal_resolver.assert_not_called()

    def test_media_connection_rejects_ipv6_site_local_before_socket(self) -> None:
        public_answer = _resolver_for("2606:4700:4700::1111")(
            "cdn.example",
            443,
        )[0]
        site_local_answer = _resolver_for("fec0::1")(
            "cdn.example",
            443,
        )[0]
        for label, host, answers, literal in (
            ("literal", "fec0::1", [site_local_answer], True),
            ("DNS only", "cdn.example", [site_local_answer], False),
            (
                "mixed public and site-local",
                "cdn.example",
                [public_answer, site_local_answer],
                False,
            ),
        ):
            resolver = Mock(return_value=answers)
            socket_factory = Mock()
            with self.subTest(label=label):
                with self.assertRaises(DiscordMediaSecurityError):
                    discord_api_module._create_public_connection(
                        (host, 443),
                        resolver=resolver,
                        socket_factory=socket_factory,
                    )
                socket_factory.assert_not_called()
                if literal:
                    resolver.assert_not_called()
                else:
                    resolver.assert_called_once()

    def test_gaierror_etimedout_is_classified_as_resolver_timeout(self) -> None:
        socket_factory = Mock()
        with self.assertRaises(DiscordMediaResolutionError) as caught:
            discord_api_module._create_public_connection(
                ("cdn.example", 443),
                resolver=Mock(
                    side_effect=socket.gaierror(errno.ETIMEDOUT, "timed out")
                ),
                socket_factory=socket_factory,
            )
        self.assertEqual(caught.exception.reason_code, "resolver_timeout")
        socket_factory.assert_not_called()

    def test_public_cdn_media_is_allowed_without_bot_authorization(self) -> None:
        requests: list[object] = []

        def opener(request: object, *, timeout: float) -> _Response:
            del timeout
            requests.append(request)
            return _Response(b"public")

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=_resolver_for("93.184.216.34"),
        )
        with transport.open_byte_stream("https://cdn.example/file") as stream:
            self.assertEqual(list(stream), [b"public"])
        self.assertEqual(len(requests), 1)
        self.assertNotIn(
            "Authorization",
            dict(requests[0].header_items()),  # type: ignore[attr-defined]
        )

    def test_iso8601_cursor_encodes_plus_sign(self) -> None:
        requests: list[object] = []

        def opener(request: object, *, timeout: float) -> _Response:
            del timeout
            requests.append(request)
            return _Response(b"[]")

        transport = DiscordHTTPTransport(self.token, opener=opener)
        transport.get_json(
            "/channels/123/threads/archived/public",
            {"before": "2026-07-19T12:34:56+00:00", "limit": 100},
        )

        url = requests[0].full_url  # type: ignore[attr-defined]
        self.assertIn("before=2026-07-19T12%3A34%3A56%2B00%3A00", url)
        self.assertNotIn("+00%3A00", url)

    def test_429_uses_json_retry_after(self) -> None:
        rate_limit_error = _http_error(
            429,
            b'{"message":"rate limited","retry_after":1.25}',
        )
        outcomes: list[object] = [
            rate_limit_error,
            _Response(b'{"ok":true}'),
        ]
        sleeps: list[float] = []

        def opener(_request: object, *, timeout: float) -> _Response:
            del timeout
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome  # type: ignore[return-value]

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            sleep=sleeps.append,
        )

        self.assertEqual(transport.get_json("/test"), {"ok": True})
        self.assertEqual(sleeps, [1.25])
        self.assertTrue(rate_limit_error.fp.closed)

    def test_5xx_retry_is_bounded_and_deterministic(self) -> None:
        calls = 0
        sleeps: list[float] = []

        def opener(_request: object, *, timeout: float) -> _Response:
            nonlocal calls
            del timeout
            calls += 1
            raise _http_error(503, b'{"message":"unavailable"}')

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            sleep=sleeps.append,
            max_retries=2,
            retry_backoff=0.5,
        )

        with self.assertRaises(DiscordAPIError) as caught:
            transport.get_json("/test")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(calls, 3)
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_401_immediately_identifies_invalid_bot_token(self) -> None:
        calls = 0

        def opener(_request: object, *, timeout: float) -> _Response:
            nonlocal calls
            del timeout
            calls += 1
            raise _http_error(401, b'{"message":"401: Unauthorized"}')

        transport = DiscordHTTPTransport(self.token, opener=opener, max_retries=5)

        with self.assertRaises(DiscordAPIError) as caught:
            transport.get_json("/users/@me")
        self.assertEqual(calls, 1)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIn("invalid bot token", str(caught.exception).lower())
        self.assertNotIn(self.token, repr(caught.exception))

    def test_absolute_media_stream_omits_authorization_and_yields_chunks(self) -> None:
        requests: list[object] = []

        def opener(request: object, *, timeout: float) -> _Response:
            del timeout
            requests.append(request)
            return _Response(b"", chunks=[b"abc", b"def"])

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=_resolver_for("93.184.216.34"),
        )
        chunks = list(
            transport.iter_bytes(
                "https://cdn.discordapp.com/attachments/1/2/file.bin",
                chunk_size=3,
            )
        )

        self.assertEqual(chunks, [b"abc", b"def"])
        self.assertNotIn(
            "Authorization",
            dict(requests[0].header_items()),  # type: ignore[attr-defined]
        )

    def test_media_stream_exposes_content_metadata_without_buffering(self) -> None:
        response = _Response(
            b"",
            chunks=[b"abc", b"def"],
            headers={"Content-Type": "image/png", "Content-Length": "6"},
        )

        def opener(_request: object, *, timeout: float) -> _Response:
            del timeout
            return response

        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=_resolver_for("93.184.216.34"),
        )

        with transport.open_byte_stream(
            "https://cdn.discordapp.com/attachments/1/2/image.png",
            chunk_size=3,
        ) as stream:
            self.assertEqual(stream.content_type, "image/png")
            self.assertEqual(stream.content_length, 6)
            self.assertEqual(list(stream), [b"abc", b"def"])
        self.assertTrue(response.closed)

    def test_media_error_redacts_token_from_url_and_diagnostic(self) -> None:
        def rejecting_opener(request: object, *, timeout: float) -> object:
            del timeout
            raise RuntimeError(f"rejected {request.full_url}")  # type: ignore[attr-defined]

        transport = DiscordHTTPTransport(
            self.token,
            opener=rejecting_opener,
            resolver=_resolver_for("93.184.216.34"),
        )

        with self.assertRaises(DiscordAPIError) as caught:
            list(
                transport.iter_bytes(
                    f"https://cdn.discordapp.com/file?signature={self.token}"
                )
            )
        self.assertNotIn(self.token, str(caught.exception))
        self.assertNotIn(self.token, repr(caught.exception))


class _ScriptedTransport:
    def __init__(self, script: list[tuple[str, dict[str, object], object]]) -> None:
        self._script = list(script)

    def get_json(self, path: str, params: dict[str, object]) -> object:
        if not self._script:
            raise AssertionError(f"unexpected request: {path} {params!r}")
        expected_path, expected_params, payload = self._script.pop(0)
        if (path, params) != (expected_path, expected_params):
            raise AssertionError(
                f"expected {(expected_path, expected_params)!r}, got {(path, params)!r}"
            )
        return payload

    def assert_exhausted(self, testcase: unittest.TestCase) -> None:
        testcase.assertEqual(self._script, [])


class DiscordPaginationTests(unittest.TestCase):
    def test_messages_use_last_message_id_until_empty_page(self) -> None:
        first_payload = [
            {"id": "300", "content": "newer", "unknown": {"keep": True}},
            {"id": "200", "content": "older"},
        ]
        terminal_payload: list[object] = []
        transport = _ScriptedTransport(
            [
                ("/channels/42/messages", {"limit": 100}, first_payload),
                (
                    "/channels/42/messages",
                    {"limit": 100, "before": "200"},
                    terminal_payload,
                ),
            ]
        )

        pages = list(iter_message_pages(transport, "42"))

        self.assertEqual(len(pages), 2)
        self.assertIs(pages[0].raw_payload, first_payload)
        self.assertEqual(pages[0].path, "/channels/42/messages")
        self.assertEqual(pages[0].params, {"limit": 100})
        self.assertEqual(pages[0].item_count, 2)
        self.assertEqual(pages[0].next_cursor, "200")
        self.assertIsNone(pages[0].terminal_status)
        self.assertIs(pages[1].raw_payload, terminal_payload)
        self.assertEqual(pages[1].item_count, 0)
        self.assertIsNone(pages[1].next_cursor)
        self.assertEqual(pages[1].terminal_status, "complete")
        transport.assert_exhausted(self)

    def test_public_archived_threads_use_archive_timestamp(self) -> None:
        first_payload = {
            "threads": [
                {
                    "id": "900",
                    "thread_metadata": {
                        "archive_timestamp": "2026-07-19T12:34:56+00:00",
                        "unknown": "retained",
                    },
                }
            ],
            "members": [],
            "has_more": True,
            "future_field": 7,
        }
        final_payload = {"threads": [], "members": [], "has_more": False}
        transport = _ScriptedTransport(
            [
                (
                    "/channels/10/threads/archived/public",
                    {"limit": 100},
                    first_payload,
                ),
                (
                    "/channels/10/threads/archived/public",
                    {
                        "limit": 100,
                        "before": "2026-07-19T12:34:56+00:00",
                    },
                    final_payload,
                ),
            ]
        )

        pages = list(iter_public_archived_thread_pages(transport, "10"))

        self.assertIs(pages[0].raw_payload, first_payload)
        self.assertEqual(pages[0].next_cursor, "2026-07-19T12:34:56+00:00")
        self.assertIsNone(pages[0].terminal_status)
        self.assertEqual(pages[1].terminal_status, "complete")
        transport.assert_exhausted(self)

    def test_private_archived_threads_use_archive_timestamp(self) -> None:
        timestamp = "2026-06-01T00:00:00+00:00"
        payload = {
            "threads": [
                {"id": "800", "thread_metadata": {"archive_timestamp": timestamp}}
            ],
            "has_more": False,
            "unknown": [1, 2, 3],
        }
        transport = _ScriptedTransport(
            [
                (
                    "/channels/11/threads/archived/private",
                    {"limit": 100, "before": "start"},
                    payload,
                )
            ]
        )

        pages = list(
            iter_private_archived_thread_pages(transport, "11", before="start")
        )

        self.assertIs(pages[0].raw_payload, payload)
        self.assertEqual(pages[0].item_count, 1)
        self.assertIsNone(pages[0].next_cursor)
        self.assertEqual(pages[0].terminal_status, "complete")
        transport.assert_exhausted(self)

    def test_joined_private_archived_threads_use_thread_id(self) -> None:
        first_payload = {
            "threads": [
                {
                    "id": "700",
                    "thread_metadata": {
                        "archive_timestamp": "2026-01-01T00:00:00+00:00"
                    },
                }
            ],
            "has_more": True,
        }
        final_payload = {"threads": [], "has_more": False}
        transport = _ScriptedTransport(
            [
                (
                    "/channels/12/users/@me/threads/archived/private",
                    {"limit": 100},
                    first_payload,
                ),
                (
                    "/channels/12/users/@me/threads/archived/private",
                    {"limit": 100, "before": "700"},
                    final_payload,
                ),
            ]
        )

        pages = list(iter_joined_private_archived_thread_pages(transport, "12"))

        self.assertEqual(pages[0].next_cursor, "700")
        self.assertEqual(pages[1].terminal_status, "complete")
        transport.assert_exhausted(self)

    def test_new_pins_endpoint_uses_pinned_at_cursor(self) -> None:
        pinned_at = "2026-07-18T11:22:33+00:00"
        first_payload = {
            "items": [
                {
                    "pinned_at": pinned_at,
                    "message": {"id": "123", "unknown": "retained"},
                }
            ],
            "has_more": True,
            "unknown": {"retained": True},
        }
        final_payload = {"items": [], "has_more": False}
        transport = _ScriptedTransport(
            [
                (
                    "/channels/13/messages/pins",
                    {"limit": 50},
                    first_payload,
                ),
                (
                    "/channels/13/messages/pins",
                    {"limit": 50, "before": pinned_at},
                    final_payload,
                ),
            ]
        )

        pages = list(iter_pin_pages(transport, "13"))

        self.assertIs(pages[0].raw_payload, first_payload)
        self.assertEqual(pages[0].next_cursor, pinned_at)
        self.assertEqual(pages[0].item_count, 1)
        self.assertEqual(pages[1].terminal_status, "complete")
        transport.assert_exhausted(self)

    def test_has_more_with_empty_page_is_failed_with_diagnostic(self) -> None:
        payload = {"threads": [], "has_more": True}
        transport = _ScriptedTransport(
            [
                (
                    "/channels/14/threads/archived/public",
                    {"limit": 100},
                    payload,
                )
            ]
        )

        pages = list(iter_public_archived_thread_pages(transport, "14"))

        self.assertEqual(pages[0].terminal_status, "failed")
        self.assertIn("has_more", pages[0].diagnostic or "")
        self.assertIsNone(pages[0].next_cursor)
        transport.assert_exhausted(self)

    def test_has_more_with_nonadvancing_cursor_is_failed_with_diagnostic(self) -> None:
        payload = {
            "items": [{"pinned_at": "same", "message": {"id": "123"}}],
            "has_more": True,
        }
        transport = _ScriptedTransport(
            [
                (
                    "/channels/15/messages/pins",
                    {"limit": 50, "before": "same"},
                    payload,
                )
            ]
        )

        pages = list(iter_pin_pages(transport, "15", before="same"))

        self.assertEqual(pages[0].terminal_status, "failed")
        self.assertIn("did not advance", pages[0].diagnostic or "")
        self.assertIsNone(pages[0].next_cursor)
        transport.assert_exhausted(self)

    def test_max_pages_is_explicitly_truncated(self) -> None:
        payload = [{"id": "100", "content": "oldest fetched"}]
        transport = _ScriptedTransport(
            [("/channels/16/messages", {"limit": 100}, payload)]
        )

        pages = list(iter_message_pages(transport, "16", max_pages=1))

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].next_cursor, "100")
        self.assertEqual(pages[0].terminal_status, "truncated_by_limit")
        transport.assert_exhausted(self)

    def test_messages_reject_multi_page_cursor_cycle(self) -> None:
        transport = _ScriptedTransport(
            [
                ("/channels/17/messages", {"limit": 100}, [{"id": "300"}]),
                (
                    "/channels/17/messages",
                    {"limit": 100, "before": "300"},
                    [{"id": "200"}],
                ),
                (
                    "/channels/17/messages",
                    {"limit": 100, "before": "200"},
                    [{"id": "300"}],
                ),
            ]
        )

        pages = list(iter_message_pages(transport, "17"))

        self.assertEqual(pages[-1].terminal_status, "failed")
        self.assertIn("seen cursor", pages[-1].diagnostic or "")
        transport.assert_exhausted(self)

    def test_archived_threads_reject_wrong_direction_timestamp(self) -> None:
        before = "2026-07-19T12:00:00+00:00"
        payload = {
            "threads": [
                {
                    "id": "100",
                    "thread_metadata": {
                        "archive_timestamp": "2026-07-20T12:00:00+00:00"
                    },
                }
            ],
            "has_more": True,
        }
        transport = _ScriptedTransport(
            [
                (
                    "/channels/18/threads/archived/public",
                    {"limit": 100, "before": before},
                    payload,
                )
            ]
        )

        pages = list(
            iter_public_archived_thread_pages(transport, "18", before=before)
        )

        self.assertEqual(pages[0].terminal_status, "failed")
        self.assertIn("older", pages[0].diagnostic or "")
        transport.assert_exhausted(self)

    def test_pins_treat_naive_before_as_utc_without_crashing(self) -> None:
        before = "2026-07-19T12:00:00"
        payload = {
            "items": [
                {
                    "pinned_at": "2026-07-20T12:00:00+00:00",
                    "message": {"id": "101", "unknown": "retained"},
                }
            ],
            "has_more": True,
            "unknown": {"retained": True},
        }
        transport = _ScriptedTransport(
            [
                (
                    "/channels/20/messages/pins",
                    {"limit": 50, "before": before},
                    payload,
                )
            ]
        )

        pages = list(iter_pin_pages(transport, "20", before=before))

        self.assertEqual(len(pages), 1)
        self.assertIs(pages[0].raw_payload, payload)
        self.assertEqual(pages[0].terminal_status, "failed")
        self.assertIn("older", pages[0].diagnostic or "")
        transport.assert_exhausted(self)

    def test_joined_threads_reject_wrong_direction_snowflake(self) -> None:
        payload = {"threads": [{"id": "600"}], "has_more": True}
        transport = _ScriptedTransport(
            [
                (
                    "/channels/19/users/@me/threads/archived/private",
                    {"limit": 100, "before": "500"},
                    payload,
                )
            ]
        )

        pages = list(
            iter_joined_private_archived_thread_pages(
                transport,
                "19",
                before="500",
            )
        )

        self.assertEqual(pages[0].terminal_status, "failed")
        self.assertIn("smaller", pages[0].diagnostic or "")
        transport.assert_exhausted(self)


if __name__ == "__main__":
    unittest.main()
