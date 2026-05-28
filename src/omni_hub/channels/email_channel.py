"""Email Channel — stdlib-only IMAP poll + SMTP send.

Builds on ``imaplib`` + ``smtplib`` + ``email`` (all in stdlib) so no
third-party dependency is added to the main repo.  Configure via
``EmailChannelConfig`` or environment variables:

* ``OMNI_EMAIL_IMAP_HOST``  / ``OMNI_EMAIL_IMAP_USER`` / ``OMNI_EMAIL_IMAP_PASSWORD``
* ``OMNI_EMAIL_SMTP_HOST``  / ``OMNI_EMAIL_SMTP_USER`` / ``OMNI_EMAIL_SMTP_PASSWORD``
* ``OMNI_EMAIL_FROM``        (defaults to IMAP user)
* ``OMNI_EMAIL_INBOX``       (defaults to ``INBOX``)
* ``OMNI_EMAIL_MARK_SEEN``   (defaults to true — set false in tests)

Health check does NOT connect — it just confirms config is present.  A
real probe happens when ``listen()`` opens the IMAP socket.

Security note: passwords flow through env or the explicit config object.
**Never** persist them to disk in this module; storage is the user's
responsibility (1Password, macOS keychain, etc.).  The Policy engine's
EXTERNAL_SEND risk tier applies to ``reply()``.
"""

from __future__ import annotations

import email
import imaplib
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Iterator

from .base import Channel, ChannelHealth, InboundMessage, OutboundMessage


@dataclass(slots=True)
class EmailChannelConfig:
    imap_host: str
    imap_user: str
    imap_password: str
    smtp_host: str
    smtp_user: str
    smtp_password: str
    imap_port: int = 993
    smtp_port: int = 465
    mailbox: str = "INBOX"
    from_addr: str = ""               # defaults to imap_user
    mark_seen: bool = True            # ack messages by setting \Seen after yield

    @classmethod
    def from_env(cls) -> "EmailChannelConfig | None":
        """Build a config from environment variables.  Returns None if any
        required variable is unset (so health_check can report 'not
        configured' rather than crash)."""

        required = {
            "imap_host":      "OMNI_EMAIL_IMAP_HOST",
            "imap_user":      "OMNI_EMAIL_IMAP_USER",
            "imap_password":  "OMNI_EMAIL_IMAP_PASSWORD",
            "smtp_host":      "OMNI_EMAIL_SMTP_HOST",
            "smtp_user":      "OMNI_EMAIL_SMTP_USER",
            "smtp_password":  "OMNI_EMAIL_SMTP_PASSWORD",
        }
        values: dict[str, str] = {}
        for field_name, env_name in required.items():
            value = os.environ.get(env_name, "")
            if not value:
                return None
            values[field_name] = value
        return cls(
            mailbox=os.environ.get("OMNI_EMAIL_INBOX", "INBOX"),
            from_addr=os.environ.get("OMNI_EMAIL_FROM", values["imap_user"]),
            mark_seen=os.environ.get("OMNI_EMAIL_MARK_SEEN", "1") not in {"0", "false", "False"},
            **values,
        )

    def __post_init__(self) -> None:
        if not self.from_addr:
            self.from_addr = self.imap_user


class EmailChannel:
    name = "email"

    def __init__(self, config: EmailChannelConfig | None = None) -> None:
        self.config = config or EmailChannelConfig.from_env()
        self._imap: imaplib.IMAP4_SSL | None = None
        self._ssl_context = ssl.create_default_context()

    def configured(self) -> bool:
        return self.config is not None

    def health_check(self) -> ChannelHealth:
        if not self.configured():
            return ChannelHealth(
                name=self.name, ok=False,
                detail={"error": "OMNI_EMAIL_* env vars not set"},
            )
        return ChannelHealth(
            name=self.name, ok=True,
            detail={
                "imap_host": self.config.imap_host,
                "smtp_host": self.config.smtp_host,
                "mailbox":   self.config.mailbox,
                "from":      self.config.from_addr,
            },
        )

    # ---- listen --------------------------------------------------

    def listen(self) -> Iterator[InboundMessage]:
        if not self.configured():
            raise RuntimeError("EmailChannel is not configured (OMNI_EMAIL_* env vars missing)")
        assert self.config is not None
        self._connect_imap()
        assert self._imap is not None
        try:
            self._imap.select(self.config.mailbox)
            status, data = self._imap.search(None, "UNSEEN")
            if status != "OK":
                return
            ids = data[0].split() if data else []
            for raw_id in ids:
                inbound = self._fetch_one(raw_id)
                if inbound is None:
                    continue
                yield inbound
                if self.config.mark_seen:
                    self._imap.store(raw_id, "+FLAGS", "\\Seen")
        finally:
            self._disconnect_imap()

    def _fetch_one(self, raw_id: bytes) -> InboundMessage | None:
        assert self._imap is not None
        status, data = self._imap.fetch(raw_id, "(RFC822)")
        if status != "OK" or not data or data[0] is None:
            return None
        raw = data[0][1]
        if not isinstance(raw, (bytes, bytearray)):
            return None
        msg = email.message_from_bytes(raw)
        sender = str(msg.get("From", "")).strip()
        subject = str(msg.get("Subject", "")).strip()
        message_id = str(msg.get("Message-Id", "")).strip()
        body = _extract_plain_body(msg)
        return InboundMessage.new(
            channel=self.name,
            sender=sender,
            body=body,
            subject=subject,
            metadata={
                "message_id": message_id,
                "raw_uid": raw_id.decode("ascii", errors="ignore"),
            },
        )

    # ---- reply ---------------------------------------------------

    def reply(self, msg: OutboundMessage) -> None:
        if not self.configured():
            raise RuntimeError("EmailChannel is not configured")
        assert self.config is not None
        message = EmailMessage()
        message["From"] = self.config.from_addr
        message["To"] = msg.recipient
        message["Subject"] = msg.subject or "Re: (no subject)"
        if msg.in_reply_to:
            message["In-Reply-To"] = msg.in_reply_to
            message["References"] = msg.in_reply_to
        # X-Trace header makes audit alignment trivial server-side.
        message["X-Omni-Trace-Id"] = msg.trace_id
        message.set_content(msg.body)
        with smtplib.SMTP_SSL(
            self.config.smtp_host, self.config.smtp_port, context=self._ssl_context,
        ) as smtp:
            smtp.login(self.config.smtp_user, self.config.smtp_password)
            smtp.send_message(message)

    # ---- lifecycle -----------------------------------------------

    def _connect_imap(self) -> None:
        assert self.config is not None
        self._imap = imaplib.IMAP4_SSL(
            self.config.imap_host, self.config.imap_port,
            ssl_context=self._ssl_context,
        )
        self._imap.login(self.config.imap_user, self.config.imap_password)

    def _disconnect_imap(self) -> None:
        if self._imap is not None:
            try:
                self._imap.close()
            except Exception:                                       # noqa: BLE001
                pass
            try:
                self._imap.logout()
            except Exception:                                       # noqa: BLE001
                pass
            self._imap = None

    def shutdown(self) -> None:
        self._disconnect_imap()


def _extract_plain_body(msg: email.message.Message) -> str:
    """Walk a MIME tree and return the first text/plain body, falling back
    to text/html stripped of tags."""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, (bytes, bytearray)):
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        # Fall back to text/html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if isinstance(payload, (bytes, bytearray)):
                    return _strip_html(payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace",
                    ))
        return ""
    payload = msg.get_payload(decode=True)
    if isinstance(payload, (bytes, bytearray)):
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            return _strip_html(text)
        return text
    return ""


def _strip_html(html: str) -> str:
    """Very simple tag stripper — stdlib only, no BeautifulSoup."""

    import re
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


__all__ = ["EmailChannel", "EmailChannelConfig"]
