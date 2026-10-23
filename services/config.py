"""Find the app configuration.

Order: Streamlit secrets (.streamlit/secrets.toml locally, the Secrets box on Streamlit Cloud), then a TOML file
mounted into the container (Cloud Run mounts the Secret Manager secret at /etc/secrets/secrets.toml; override the
path with the MOBIQ_SECRETS_FILE environment variable). When nothing usable is found the error says where it
looked, listing section NAMES only, never values.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Mapping

from services.repository import StorageError

FALLBACK_FILES = ("/etc/secrets/secrets.toml",)
REQUIRED_SECTION = "gsheets"


def load_config(streamlit_secrets, fallback_files: tuple[str, ...] = FALLBACK_FILES) -> Mapping:
    notes: list[str] = []
    try:
        if REQUIRED_SECTION in streamlit_secrets:
            return streamlit_secrets
        notes.append("Streamlit secrets have no [gsheets] section")
    except Exception as exc:  # st.secrets raises when no secrets file exists
        notes.append(f"Streamlit secrets are unavailable ({type(exc).__name__})")

    for path in [p for p in (os.environ.get("MOBIQ_SECRETS_FILE"), *fallback_files) if p]:
        file = Path(path)
        if not file.is_file():
            notes.append(f"{path}: not found")
            continue
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            notes.append(f"{path}: cannot be read ({type(exc).__name__})")
            continue
        except tomllib.TOMLDecodeError:
            notes.append(f"{path}: is not valid TOML")
            continue
        if REQUIRED_SECTION in data:
            return data
        notes.append(f"{path}: found, but its sections are {sorted(data) or 'empty'}")

    raise StorageError("Google Sheets is not configured. Looked in: " + "; ".join(notes) + ".")
