"""Outbound email. One SMTP implementation shared by sign-in codes and payment reminders."""
from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol


class DeliveryError(Exception):
    pass


class Mailer(Protocol):
    def send_message(self, to: str, subject: str, body: str) -> None: ...


class SmtpMailer:
    def __init__(self, host: str, port: int, username: str, password: str, sender: str, use_ssl: bool = False):
        self._cfg = (host, int(port), username, password, sender, use_ssl)

    def send_message(self, to: str, subject: str, body: str) -> None:
        host, port, user, pw, sender, use_ssl = self._cfg
        msg = EmailMessage()
        msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")  # no header injection
        msg["From"] = sender
        msg["To"] = to
        msg.set_content(body)
        try:
            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=15)
            else:
                server = smtplib.SMTP(host, port, timeout=15)
                server.starttls()
            with server:
                if user:
                    server.login(user, pw)
                server.send_message(msg)
        except (smtplib.SMTPException, OSError):
            raise DeliveryError("Could not send the email.") from None


@dataclass
class OutboxMailer:
    """DEMO/TEST ONLY: records messages in memory instead of sending them."""
    sent: list[tuple[str, str, str]] = field(default_factory=list)
    simulated = True

    def send_message(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))
