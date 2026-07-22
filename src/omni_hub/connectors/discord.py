"""Credential-safe, stdlib-only access to the Discord REST API."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import enum
import errno
from functools import partial
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
import time
from typing import Any, Protocol
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True, slots=True)
class DiscordPage:
    """One unmodified Discord payload plus its pagination evidence."""

    raw_payload: Any
    path: str
    params: Mapping[str, object]
    item_count: int
    next_cursor: str | None
    terminal_status: str | None = None
    diagnostic: str | None = None


class DiscordJSONTransport(Protocol):
    """Narrow seam used by endpoint paginators and scripted tests."""

    base_url: str

    def get_json(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> Any: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn redirects into HTTP errors before credentials can cross origins."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class DiscordByteStream:
    """Closeable streaming response with the media metadata Task 3 needs."""

    def __init__(self, response: Any, *, chunk_size: int) -> None:
        self._response = response
        self.chunk_size = chunk_size
        raw_headers = getattr(response, "headers", {})
        self.headers = {
            str(key): str(value)
            for key, value in raw_headers.items()
        }
        raw_content_type = self._header("Content-Type")
        self.content_type = (
            raw_content_type.split(";", 1)[0].strip().lower()
            if raw_content_type
            else None
        )
        raw_content_length = self._header("Content-Length")
        try:
            parsed_length = int(raw_content_length) if raw_content_length is not None else None
        except ValueError:
            parsed_length = None
        self.content_length = (
            parsed_length if parsed_length is not None and parsed_length >= 0 else None
        )

    def __enter__(self) -> DiscordByteStream:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[bytes]:
        while True:
            chunk = self._response.read(self.chunk_size)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        self._response.close()

    def _header(self, wanted: str) -> str | None:
        wanted_lower = wanted.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted_lower:
                return value
        return None


class DiscordAPIError(RuntimeError):
    """A Discord transport error that never retains request credentials."""

    __slots__ = ("status_code", "path", "diagnostic")

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        path: str | None = None,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path
        self.diagnostic = diagnostic

    def __repr__(self) -> str:
        return (
            f"DiscordAPIError({str(self)!r}, status_code={self.status_code!r}, "
            f"path={self.path!r}, diagnostic={self.diagnostic!r})"
        )


class DiscordMediaSecurityError(DiscordAPIError):
    """A media URL failed the public-network-only connection policy."""


class DiscordMediaResolutionReason(enum.StrEnum):
    EAI_AGAIN = "resolver_eai_again"
    TIMEOUT = "resolver_timeout"
    NAME_NOT_FOUND = "resolver_name_not_found"
    NO_DATA = "resolver_no_data"
    EMPTY_ANSWER = "resolver_empty_answer"
    OS_ERROR_UNCLASSIFIED = "resolver_os_error_unclassified"
    INVALID_ANSWER = "resolver_invalid_answer"


class DiscordMediaResolutionError(DiscordAPIError):
    __slots__ = ("reason_code",)

    def __init__(
        self,
        reason_code: DiscordMediaResolutionReason,
        *,
        path: str | None = None,
    ) -> None:
        super().__init__("Discord media host resolution failed", path=path)
        self.reason_code = str(reason_code)


class DiscordMediaResolutionInvalidAnswer(DiscordAPIError):
    __slots__ = ("reason_code",)

    def __init__(self, *, path: str | None = None) -> None:
        super().__init__("Discord media resolver returned an invalid answer", path=path)
        self.reason_code = str(DiscordMediaResolutionReason.INVALID_ANSWER)


_RFC2544_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
RFC2544_FAKE_IP_MEDIA_NETWORK = str(_RFC2544_FAKE_IP_NETWORK)
RFC2544_FAKE_IP_MEDIA_PORT = 443
RFC2544_FAKE_IP_MEDIA_POLICY_VERSION = "rfc2544_discord_media_v1"
RFC2544_FAKE_IP_MEDIA_HOSTS = (
    "cdn.discordapp.com",
    "images-ext-1.discordapp.net",
    "images-ext-2.discordapp.net",
    "media.discordapp.net",
)
RFC2544_FAKE_IP_MEDIA_HOSTS_SHA256 = hashlib.sha256(
    json.dumps(
        list(RFC2544_FAKE_IP_MEDIA_HOSTS),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
).hexdigest()


def rfc2544_fake_ip_media_policy_descriptor() -> dict[str, object]:
    """Return the immutable policy inputs recorded in audit/request evidence."""

    inputs: dict[str, object] = {
        "version": RFC2544_FAKE_IP_MEDIA_POLICY_VERSION,
        "network": RFC2544_FAKE_IP_MEDIA_NETWORK,
        "port": RFC2544_FAKE_IP_MEDIA_PORT,
        "hosts": list(RFC2544_FAKE_IP_MEDIA_HOSTS),
        "hosts_sha256": RFC2544_FAKE_IP_MEDIA_HOSTS_SHA256,
    }
    return {
        **inputs,
        "inputs_sha256": hashlib.sha256(
            json.dumps(
                inputs,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest(),
    }


def _literal_ip_address(
    host: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Recognize canonical and legacy numeric IP literal spellings without DNS."""

    normalized = host.rstrip(".").split("%", 1)[0]
    try:
        return ipaddress.ip_address(normalized)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_aton(normalized))
    except OSError:
        return None


def _canonical_media_hostname(host: str) -> str | None:
    if host.endswith(".."):
        return None
    normalized = host[:-1] if host.endswith(".") else host
    if not normalized:
        return None
    try:
        return normalized.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def _media_origin(parsed: urllib.parse.SplitResult) -> str | None:
    hostname = parsed.hostname
    if parsed.scheme and hostname:
        safe_host = f"[{hostname}]" if ":" in hostname else hostname
        return f"{parsed.scheme.lower()}://{safe_host}/"
    return None


def _is_public_unicast_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Return whether an address is globally routable unicast evidence."""

    return address.is_global and not (
        address.is_multicast
        or getattr(address, "is_site_local", False)
        or address.is_reserved
    )


def _resolved_public_addresses(
    host: str,
    port: int,
    *,
    resolver: Callable[..., object],
    path: str | None,
    allow_rfc2544_fake_ip: bool = False,
) -> list[tuple[int, int, int, tuple[Any, ...]]]:
    literal_address = _literal_ip_address(host)
    canonical_hostname = _canonical_media_hostname(host)
    if literal_address is not None and not _is_public_unicast_address(
        literal_address
    ):
        raise DiscordMediaSecurityError(
            "Discord media host is a non-public network address",
            path=path,
        )
    try:
        raw_answers = resolver(host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if exc.errno == socket.EAI_AGAIN:
            reason = DiscordMediaResolutionReason.EAI_AGAIN
        elif exc.errno == errno.ETIMEDOUT:
            reason = DiscordMediaResolutionReason.TIMEOUT
        elif exc.errno == socket.EAI_NONAME:
            reason = DiscordMediaResolutionReason.NAME_NOT_FOUND
        elif (
            (eai_nodata := getattr(socket, "EAI_NODATA", None)) is not None
            and eai_nodata != socket.EAI_NONAME
            and exc.errno == eai_nodata
        ):
            reason = DiscordMediaResolutionReason.NO_DATA
        else:
            reason = DiscordMediaResolutionReason.OS_ERROR_UNCLASSIFIED
        raise DiscordMediaResolutionError(reason, path=path) from None
    except TimeoutError:
        raise DiscordMediaResolutionError(
            DiscordMediaResolutionReason.TIMEOUT,
            path=path,
        ) from None
    except OSError as exc:
        reason = (
            DiscordMediaResolutionReason.TIMEOUT
            if exc.errno == errno.ETIMEDOUT
            else DiscordMediaResolutionReason.OS_ERROR_UNCLASSIFIED
        )
        raise DiscordMediaResolutionError(reason, path=path) from None
    if isinstance(raw_answers, (list, tuple)) and not raw_answers:
        raise DiscordMediaResolutionError(
            DiscordMediaResolutionReason.EMPTY_ANSWER,
            path=path,
        )
    if not isinstance(raw_answers, (list, tuple)):
        raise DiscordMediaResolutionInvalidAnswer(path=path)
    approved: list[tuple[int, int, int, tuple[Any, ...]]] = []
    for answer in raw_answers:
        if not isinstance(answer, tuple) or len(answer) != 5:
            raise DiscordMediaResolutionInvalidAnswer(path=path)
        family, socket_type, protocol, _canonical_name, socket_address = answer
        expected_length = 2 if family == socket.AF_INET else 4
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or isinstance(socket_type, bool)
            or socket_type != socket.SOCK_STREAM
            or isinstance(protocol, bool)
            or protocol not in {0, socket.IPPROTO_TCP}
            or not isinstance(socket_address, tuple)
            or len(socket_address) != expected_length
            or not isinstance(socket_address[0], str)
            or isinstance(socket_address[1], bool)
            or not isinstance(socket_address[1], int)
            or not 1 <= socket_address[1] <= 65535
            or socket_address[1] != port
        ):
            raise DiscordMediaResolutionInvalidAnswer(path=path)
        if family == socket.AF_INET6 and (
            isinstance(socket_address[2], bool)
            or not isinstance(socket_address[2], int)
            or not 0 <= socket_address[2] < (1 << 20)
            or isinstance(socket_address[3], bool)
            or not isinstance(socket_address[3], int)
            or not 0 <= socket_address[3] <= 0xFFFFFFFF
        ):
            raise DiscordMediaResolutionInvalidAnswer(path=path)
        try:
            if "%" in socket_address[0]:
                raise ValueError
            address = ipaddress.ip_address(socket_address[0])
        except ValueError:
            raise DiscordMediaResolutionInvalidAnswer(path=path) from None
        if (
            family == socket.AF_INET
            and not isinstance(address, ipaddress.IPv4Address)
        ) or (
            family == socket.AF_INET6
            and not isinstance(address, ipaddress.IPv6Address)
        ):
            raise DiscordMediaResolutionInvalidAnswer(path=path)
        allowed_dns_fake_ip = (
            allow_rfc2544_fake_ip
            and literal_address is None
            and canonical_hostname in RFC2544_FAKE_IP_MEDIA_HOSTS
            and port == RFC2544_FAKE_IP_MEDIA_PORT
            and isinstance(address, ipaddress.IPv4Address)
            and address in _RFC2544_FAKE_IP_NETWORK
        )
        if not _is_public_unicast_address(address) and not allowed_dns_fake_ip:
            raise DiscordMediaSecurityError(
                "Discord media host resolved to a non-public network address",
                path=path,
            )
        approved.append((family, socket_type, protocol, socket_address))
    return approved


def _validate_public_media_url(
    url: str,
    *,
    resolver: Callable[..., object],
    allow_rfc2544_fake_ip: bool = False,
) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        raise DiscordMediaSecurityError(
            "Discord media URL is invalid",
        ) from None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DiscordMediaSecurityError(
            "Discord media URL must be an absolute credential-free HTTPS URL",
            path=_media_origin(parsed),
        )
    try:
        port = parsed.port or 443
    except ValueError:
        raise DiscordMediaSecurityError(
            "Discord media URL has an invalid port",
            path=_media_origin(parsed),
        ) from None
    _resolved_public_addresses(
        parsed.hostname,
        port,
        resolver=resolver,
        path=_media_origin(parsed),
        allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
    )


def _create_public_connection(
    address: tuple[str, int],
    timeout: object = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
    *,
    resolver: Callable[..., object],
    socket_factory: Callable[..., Any] = socket.socket,
    allow_rfc2544_fake_ip: bool = False,
) -> Any:
    host, port = address
    approved = _resolved_public_addresses(
        host,
        port,
        resolver=resolver,
        path=f"https://{host}/",
        allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
    )
    last_error: OSError | None = None
    for family, socket_type, protocol, socket_address in approved:
        connection = socket_factory(family, socket_type, protocol)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address is not None:
                connection.bind(source_address)
            connection.connect(socket_address)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
    if last_error is not None:
        raise last_error
    raise DiscordMediaSecurityError(
        "Discord media host has no connectable public network address",
        path=f"https://{host}/",
    )


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        *,
        resolver: Callable[..., object],
        socket_factory: Callable[..., Any] = socket.socket,
        allow_rfc2544_fake_ip: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(host, **kwargs)
        self._create_connection = partial(
            _create_public_connection,
            resolver=resolver,
            socket_factory=socket_factory,
            allow_rfc2544_fake_ip=allow_rfc2544_fake_ip,
        )


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(
        self,
        *,
        resolver: Callable[..., object],
        allow_rfc2544_fake_ip: bool = False,
    ) -> None:
        super().__init__()
        self._resolver = resolver
        self._allow_rfc2544_fake_ip = allow_rfc2544_fake_ip

    def https_open(self, request: urllib.request.Request) -> Any:
        connection = partial(
            _PublicHTTPSConnection,
            resolver=self._resolver,
            allow_rfc2544_fake_ip=self._allow_rfc2544_fake_ip,
        )
        return self.do_open(connection, request, context=self._context)


class _PublicMediaRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        *,
        resolver: Callable[..., object],
        allow_rfc2544_fake_ip: bool = False,
    ) -> None:
        super().__init__()
        self._resolver = resolver
        self._allow_rfc2544_fake_ip = allow_rfc2544_fake_ip

    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_public_media_url(
            newurl,
            resolver=self._resolver,
            allow_rfc2544_fake_ip=self._allow_rfc2544_fake_ip,
        )
        redirected = super().redirect_request(
            request,
            fp,
            code,
            msg,
            headers,
            newurl,
        )
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def read_bot_token(path: str | os.PathLike[str]) -> str:
    """Read a Discord bot token from an owner-only, non-symlink regular file."""

    token_path = Path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(token_path, flags)
    except OSError:
        raise ValueError(f"Discord token file cannot be opened safely: {token_path}") from None

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Discord token path must be a regular file")
        if metadata.st_uid != os.getuid():
            raise ValueError("Discord token file must be owned by the current user")
        if stat.S_IMODE(metadata.st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("Discord token file must not grant group or other access")
        with os.fdopen(descriptor, "r", encoding="utf-8") as token_file:
            descriptor = -1
            token = token_file.read().strip()
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if not token:
        raise ValueError("Discord token file is empty")
    return token


class DiscordHTTPTransport:
    """Small injectable HTTP transport; endpoint pagination lives elsewhere."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://discord.com/api/v10",
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        resolver: Callable[..., object] | None = None,
        allow_rfc2544_fake_ip: bool = False,
    ) -> None:
        if not token:
            raise ValueError("Discord token must not be empty")
        parsed_base_url = urllib.parse.urlsplit(base_url)
        if (
            parsed_base_url.scheme.lower() != "https"
            or parsed_base_url.hostname is None
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError(
                "Discord API base_url must be a credential-free HTTPS URL"
            )
        try:
            parsed_base_url.port
        except ValueError:
            raise ValueError("Discord API base_url has an invalid port") from None
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_backoff < 0:
            raise ValueError("retry_backoff must be non-negative")
        if not isinstance(allow_rfc2544_fake_ip, bool):
            raise ValueError("allow_rfc2544_fake_ip must be a boolean")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.allow_rfc2544_fake_ip = allow_rfc2544_fake_ip
        self.rfc2544_fake_ip_policy = (
            rfc2544_fake_ip_media_policy_descriptor()
            if allow_rfc2544_fake_ip
            else None
        )
        self._resolver = resolver or socket.getaddrinfo
        if opener is not None:
            self._api_opener = opener
            self._media_opener = opener
        else:
            self._api_opener = urllib.request.build_opener(_NoRedirectHandler()).open
            self._media_opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                _PublicHTTPSHandler(
                    resolver=self._resolver,
                    allow_rfc2544_fake_ip=self.allow_rfc2544_fake_ip,
                ),
                _PublicMediaRedirectHandler(
                    resolver=self._resolver,
                    allow_rfc2544_fake_ip=self.allow_rfc2544_fake_ip,
                ),
            ).open
        self._sleep = sleep

    def __repr__(self) -> str:
        return (
            f"DiscordHTTPTransport(base_url={self.base_url!r}, "
            f"timeout={self.timeout!r}, max_retries={self.max_retries!r})"
        )

    def get_json(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> Any:
        """GET one Discord API response and decode its JSON without reshaping it."""

        if urllib.parse.urlsplit(path).scheme:
            raise ValueError("Discord API paths must be relative")
        safe_path = self._redact(path)
        request = self._request(path, params=params, authorize=True)
        try:
            with self._open_with_retries(request, safe_path) as response:
                body = response.read()
        except DiscordAPIError:
            raise
        except Exception as exc:  # opener implementations may raise non-urllib errors
            diagnostic = self._redact(str(exc))
            raise DiscordAPIError(
                "Discord request failed",
                path=safe_path,
                diagnostic=diagnostic,
            ) from None
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DiscordAPIError(
                "Discord returned invalid JSON",
                path=safe_path,
                diagnostic=self._redact(str(exc)),
            ) from None

    def iter_bytes(
        self,
        path_or_url: str,
        params: Mapping[str, object] | None = None,
        *,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        """Yield response bytes lazily; absolute media URLs receive no bot token."""

        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        with self.open_byte_stream(
            path_or_url,
            params,
            chunk_size=chunk_size,
        ) as stream:
            yield from stream

    def open_byte_stream(
        self,
        path_or_url: str,
        params: Mapping[str, object] | None = None,
        *,
        chunk_size: int = 64 * 1024,
    ) -> DiscordByteStream:
        """Open a lazy media stream while retaining Content-Type and length."""

        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        _validate_public_media_url(
            path_or_url,
            resolver=self._resolver,
            allow_rfc2544_fake_ip=self.allow_rfc2544_fake_ip,
        )
        request = self._request(path_or_url, params=params, authorize=False)
        safe_path = self._redact(path_or_url)
        try:
            response = self._open_with_retries(request, safe_path)
            return DiscordByteStream(response, chunk_size=chunk_size)
        except DiscordAPIError:
            raise
        except Exception as exc:
            raise DiscordAPIError(
                "Discord byte download failed",
                path=safe_path,
                diagnostic=self._redact(str(exc)),
            ) from None

    def _request(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, object] | None,
        authorize: bool,
    ) -> urllib.request.Request:
        if urllib.parse.urlsplit(path_or_url).scheme:
            url = path_or_url
        else:
            url = f"{self.base_url}/{path_or_url.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode(
                [(key, value) for key, value in params.items() if value is not None]
            )
            separator = "&" if urllib.parse.urlsplit(url).query else "?"
            url = f"{url}{separator}{query}"
        headers = {"Accept": "application/json", "User-Agent": "omni-hub-discord/2"}
        if authorize:
            headers["Authorization"] = f"Bot {self._token}"
        return urllib.request.Request(url, headers=headers, method="GET")

    def _open_with_retries(
        self,
        request: urllib.request.Request,
        path: str,
    ) -> Any:
        opener = (
            self._api_opener
            if request.has_header("Authorization")
            else self._media_opener
        )
        for retry_number in range(self.max_retries + 1):
            try:
                return opener(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                retry_after = self._retry_after(exc) if exc.code == 429 else None
                exc.close()
                if exc.code == 401:
                    raise DiscordAPIError(
                        "Discord invalid bot token (HTTP 401)",
                        status_code=401,
                        path=path,
                    ) from None
                if retry_number < self.max_retries and exc.code == 429:
                    if retry_after is not None:
                        self._sleep(retry_after)
                        continue
                if retry_number < self.max_retries and 500 <= exc.code < 600:
                    self._sleep(self.retry_backoff * (2**retry_number))
                    continue
                raise DiscordAPIError(
                    f"Discord request failed with HTTP {exc.code}",
                    status_code=exc.code,
                    path=path,
                ) from None
            except urllib.error.URLError as exc:
                raise DiscordAPIError(
                    "Discord request failed",
                    path=path,
                    diagnostic=self._redact(str(exc.reason)),
                ) from None
        raise AssertionError("retry loop exhausted without returning or raising")

    @staticmethod
    def _retry_after(error: urllib.error.HTTPError) -> float | None:
        try:
            payload = json.loads(error.read())
            retry_after = float(payload["retry_after"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return retry_after if retry_after >= 0 else None

    def _redact(self, message: str) -> str:
        return message.replace(self._token, "[REDACTED]")


def iter_message_pages(
    transport: DiscordJSONTransport,
    channel_id: str,
    *,
    before: str | None = None,
    max_pages: int | None = None,
) -> Iterator[DiscordPage]:
    """Yield raw message pages until Discord returns the required empty page."""

    _validate_max_pages(max_pages)
    path = f"/channels/{channel_id}/messages"
    cursor = before
    seen_cursors = {before} if before is not None else set()
    page_number = 0
    while True:
        params = _page_params(100, cursor)
        payload = transport.get_json(path, params)
        page_number += 1
        if not isinstance(payload, list):
            yield _failed_page(payload, path, params, "messages payload is not a list")
            return
        if not payload:
            yield DiscordPage(payload, path, params, 0, None, "complete")
            return

        next_cursor = _nested_string(payload[-1], ("id",))
        if next_cursor is None:
            yield _failed_page(
                payload,
                path,
                params,
                "messages page has no last message ID",
                item_count=len(payload),
            )
            return
        cursor_error = _cursor_progress_error(
            cursor,
            next_cursor,
            seen_cursors,
            cursor_kind="snowflake",
            label="messages",
        )
        if cursor_error is not None:
            yield _failed_page(
                payload,
                path,
                params,
                cursor_error,
                item_count=len(payload),
            )
            return
        status = "truncated_by_limit" if page_number == max_pages else None
        yield DiscordPage(payload, path, params, len(payload), next_cursor, status)
        if status is not None:
            return
        cursor = next_cursor


def iter_public_archived_thread_pages(
    transport: DiscordJSONTransport,
    channel_id: str,
    *,
    before: str | None = None,
    max_pages: int | None = None,
) -> Iterator[DiscordPage]:
    """Yield public archived threads using archive timestamps as cursors."""

    return _iter_archived_thread_pages(
        transport,
        f"/channels/{channel_id}/threads/archived/public",
        before=before,
        max_pages=max_pages,
        cursor_path=("thread_metadata", "archive_timestamp"),
        cursor_kind="timestamp",
    )


def iter_private_archived_thread_pages(
    transport: DiscordJSONTransport,
    channel_id: str,
    *,
    before: str | None = None,
    max_pages: int | None = None,
) -> Iterator[DiscordPage]:
    """Yield private archived threads using archive timestamps as cursors."""

    return _iter_archived_thread_pages(
        transport,
        f"/channels/{channel_id}/threads/archived/private",
        before=before,
        max_pages=max_pages,
        cursor_path=("thread_metadata", "archive_timestamp"),
        cursor_kind="timestamp",
    )


def iter_joined_private_archived_thread_pages(
    transport: DiscordJSONTransport,
    channel_id: str,
    *,
    before: str | None = None,
    max_pages: int | None = None,
) -> Iterator[DiscordPage]:
    """Yield joined-private archives using the last thread snowflake cursor."""

    return _iter_archived_thread_pages(
        transport,
        f"/channels/{channel_id}/users/@me/threads/archived/private",
        before=before,
        max_pages=max_pages,
        cursor_path=("id",),
        cursor_kind="snowflake",
    )


def iter_pin_pages(
    transport: DiscordJSONTransport,
    channel_id: str,
    *,
    before: str | None = None,
    max_pages: int | None = None,
) -> Iterator[DiscordPage]:
    """Yield pages from Discord's current pinned-messages endpoint."""

    _validate_max_pages(max_pages)
    path = f"/channels/{channel_id}/messages/pins"
    cursor = before
    seen_cursors = {before} if before is not None else set()
    page_number = 0
    while True:
        params = _page_params(50, cursor)
        payload = transport.get_json(path, params)
        page_number += 1
        envelope_error = _validate_envelope(payload, "items")
        if envelope_error is not None:
            yield _failed_page(payload, path, params, envelope_error)
            return
        items = payload["items"]
        has_more = payload["has_more"]
        if not has_more:
            yield DiscordPage(payload, path, params, len(items), None, "complete")
            return
        if not items:
            yield _failed_page(
                payload,
                path,
                params,
                "pins response has_more=true but contains no items",
            )
            return
        next_cursor = _nested_string(items[-1], ("pinned_at",))
        if next_cursor is None:
            yield _failed_page(
                payload,
                path,
                params,
                "pins page has no last pinned_at cursor",
                item_count=len(items),
            )
            return
        cursor_error = _cursor_progress_error(
            cursor,
            next_cursor,
            seen_cursors,
            cursor_kind="timestamp",
            label="pins",
        )
        if cursor_error is not None:
            yield _failed_page(
                payload,
                path,
                params,
                cursor_error,
                item_count=len(items),
            )
            return
        status = "truncated_by_limit" if page_number == max_pages else None
        yield DiscordPage(payload, path, params, len(items), next_cursor, status)
        if status is not None:
            return
        cursor = next_cursor


def _iter_archived_thread_pages(
    transport: DiscordJSONTransport,
    path: str,
    *,
    before: str | None,
    max_pages: int | None,
    cursor_path: tuple[str, ...],
    cursor_kind: str,
) -> Iterator[DiscordPage]:
    _validate_max_pages(max_pages)
    cursor = before
    seen_cursors = {before} if before is not None else set()
    page_number = 0
    while True:
        params = _page_params(100, cursor)
        payload = transport.get_json(path, params)
        page_number += 1
        envelope_error = _validate_envelope(payload, "threads")
        if envelope_error is not None:
            yield _failed_page(payload, path, params, envelope_error)
            return
        threads = payload["threads"]
        has_more = payload["has_more"]
        if not has_more:
            yield DiscordPage(payload, path, params, len(threads), None, "complete")
            return
        if not threads:
            yield _failed_page(
                payload,
                path,
                params,
                "archived threads response has_more=true but contains no threads",
            )
            return
        next_cursor = _nested_string(threads[-1], cursor_path)
        if next_cursor is None:
            yield _failed_page(
                payload,
                path,
                params,
                "archived threads page has no valid next cursor",
                item_count=len(threads),
            )
            return
        cursor_error = _cursor_progress_error(
            cursor,
            next_cursor,
            seen_cursors,
            cursor_kind=cursor_kind,
            label="archived threads",
        )
        if cursor_error is not None:
            yield _failed_page(
                payload,
                path,
                params,
                cursor_error,
                item_count=len(threads),
            )
            return
        status = "truncated_by_limit" if page_number == max_pages else None
        yield DiscordPage(payload, path, params, len(threads), next_cursor, status)
        if status is not None:
            return
        cursor = next_cursor


def _page_params(limit: int, cursor: str | None) -> dict[str, object]:
    params: dict[str, object] = {"limit": limit}
    if cursor is not None:
        params["before"] = cursor
    return params


def _validate_max_pages(max_pages: int | None) -> None:
    if max_pages is not None and max_pages <= 0:
        raise ValueError("max_pages must be positive")


def _validate_envelope(payload: Any, items_key: str) -> str | None:
    if not isinstance(payload, dict):
        return "Discord pagination payload is not an object"
    if not isinstance(payload.get(items_key), list):
        return f"Discord pagination payload has no {items_key} list"
    if not isinstance(payload.get("has_more"), bool):
        return "Discord pagination payload has no boolean has_more"
    return None


def _nested_string(value: Any, path: tuple[str, ...]) -> str | None:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, str) and value else None


def _cursor_progress_error(
    previous: str | None,
    next_cursor: str,
    seen_cursors: set[str],
    *,
    cursor_kind: str,
    label: str,
) -> str | None:
    if next_cursor in seen_cursors:
        if next_cursor == previous:
            return f"{label} cursor did not advance"
        return f"{label} returned a previously seen cursor"

    if cursor_kind == "snowflake":
        try:
            next_value = int(next_cursor)
            previous_value = int(previous) if previous is not None else None
        except ValueError:
            return f"{label} cursor is not a valid snowflake"
        if previous_value is not None and next_value >= previous_value:
            return f"{label} next snowflake must be smaller than the previous cursor"
    elif cursor_kind == "timestamp":
        try:
            next_value = _parse_iso8601_cursor(next_cursor)
            previous_value = (
                _parse_iso8601_cursor(previous)
                if previous is not None
                else None
            )
        except ValueError:
            return f"{label} cursor is not a valid ISO8601 timestamp"
        if previous_value is not None and next_value >= previous_value:
            return f"{label} next timestamp must be older than the previous cursor"
    else:
        raise ValueError(f"unknown cursor kind: {cursor_kind}")

    seen_cursors.add(next_cursor)
    return None


def _parse_iso8601_cursor(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _failed_page(
    payload: Any,
    path: str,
    params: Mapping[str, object],
    diagnostic: str,
    *,
    item_count: int = 0,
) -> DiscordPage:
    return DiscordPage(
        payload,
        path,
        params,
        item_count,
        None,
        "failed",
        diagnostic,
    )
