"""Shared Google OAuth token loading for Gmail and Calendar.

Two accounts on one OAuth client:
- personal: Gmail + Calendar (read, drafts, events)
- work:     Gmail, READ-ONLY

No browser login happens here: the bot runs on a headless server and would just hang.
First-time authorization runs locally: `python scripts/google_login.py personal|work`.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


PERSONAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

WORK_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

SCOPES_BY_ACCOUNT = {"personal": PERSONAL_SCOPES, "work": WORK_SCOPES}


class GoogleAuthError(RuntimeError):
    """Token is missing or revoked: re-authorize locally."""


def load_credentials(token_path: str, scopes: list[str], account: str = "personal") -> Credentials:
    hint = f"Run locally: python scripts/google_login.py {account}, then copy the token to the server."
    if not Path(token_path).exists():
        raise GoogleAuthError(f"Google ({account}) not connected: {token_path} is missing. {hint}")

    creds = Credentials.from_authorized_user_file(token_path, scopes)
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise GoogleAuthError(f"Google ({account}): token has no refresh_token. {hint}")
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise GoogleAuthError(f"Google ({account}): token revoked or expired ({e}). {hint}") from e
    Path(token_path).write_text(creds.to_json())
    return creds
