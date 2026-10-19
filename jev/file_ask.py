"""File and client helpers for the source check in jev/judge.py.

check_file reads a local source only when its name and content do not look
like a credential. _default_ask creates the API client on first use.
"""

from __future__ import annotations

import os
import threading

from jev.client import Jev

MAX_FILE_CHARS = 240000

_SECRET_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "credentials",
    "known_hosts",
}
_SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks")
_SECRET_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")

_client = None
_client_lock = threading.Lock()


def _default_ask(state: dict, questions: dict, label: str) -> dict:
    """Create and cache the API client only when a question is actually asked."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = Jev()
    return _client.ask(state, questions, label=label)


def check_file(path: str | os.PathLike) -> tuple[bool, str | None, str | None]:
    """Read a small UTF-8 text file unless its name or content looks sensitive."""
    path = os.fspath(path)
    if not os.path.isfile(path):
        return False, "not a file", None

    basename = os.path.basename(path).lower()
    if (
        basename == ".env"
        or basename.startswith(".env.")
        or basename.endswith(_SECRET_SUFFIXES)
        or basename.startswith(_SECRET_PREFIXES)
        or basename in _SECRET_NAMES
        or any(word in basename for word in ("secret", "credential", "password", "token"))
    ):
        return False, "refused: looks like a credential", None

    try:
        if os.path.getsize(path) > MAX_FILE_CHARS * 4:
            return False, "too large", None
        with open(path, "rb") as fh:
            first = fh.read(8192)
            if b"\0" in first:
                return False, "binary", None
            raw = first + fh.read()
    except OSError:
        return False, "not a file", None

    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_FILE_CHARS:
        return False, "too large", None
    if "-----BEGIN" in text and "PRIVATE KEY" in text:
        return False, "refused: contains a private key", None
    return True, None, text


