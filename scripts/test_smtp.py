"""Check the [smtp] section of .streamlit/secrets.toml without starting the app.

  python scripts/test_smtp.py                   # connect and log in only (sends nothing)
  python scripts/test_smtp.py --to you@x.com    # also send one short test email to that address

Prints only the outcome; the password is never shown.
"""
from __future__ import annotations

import argparse
import smtplib
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.mailer import SmtpMailer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", help="send a test email to this address")
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / ".streamlit" / "secrets.toml").read_text(encoding="utf-8")).get("smtp")
    if not cfg:
        print("FAIL: there is no [smtp] section in .streamlit/secrets.toml")
        return 1
    problems = []
    if "your.address" in cfg.get("username", "") or not cfg.get("username"):
        problems.append("username is still the placeholder")
    if "xxxx" in str(cfg.get("password", "")).lower() or not cfg.get("password"):
        problems.append("password is still the placeholder (use a 16-character Google app password)")
    if "your.address" in cfg.get("sender", ""):
        problems.append("sender still contains the placeholder address")
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1

    host, port, use_ssl = cfg["host"], int(cfg.get("port", 587)), bool(cfg.get("use_ssl", False))
    try:
        server = smtplib.SMTP_SSL(host, port, timeout=15) if use_ssl else smtplib.SMTP(host, port, timeout=15)
        with server:
            if not use_ssl:
                server.starttls()
            server.login(cfg["username"], cfg["password"])
        print(f"OK: connected to {host}:{port} and logged in as {cfg['username']}")
    except smtplib.SMTPAuthenticationError:
        print("FAIL: the server rejected the username/password. For Gmail you need an App Password "
              "(Google Account > Security > 2-Step Verification > App passwords), not your normal password.")
        return 1
    except (smtplib.SMTPException, OSError) as exc:
        print(f"FAIL: could not connect ({type(exc).__name__}). Check host, port and use_ssl.")
        return 1

    if args.to:
        mailer = SmtpMailer(host, port, cfg["username"], cfg["password"], cfg["sender"], use_ssl)
        try:
            mailer.send_message(args.to, "Mobiq test email", "If you can read this, Mobiq email sending works.")
            print(f"OK: test email sent to {args.to}")
        except Exception as exc:
            print(f"FAIL: login worked but sending failed ({type(exc).__name__}). Check the sender address.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
