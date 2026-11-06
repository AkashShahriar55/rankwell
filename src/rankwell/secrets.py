"""Secrets: the App Store Connect API key, in the macOS Keychain.

Environment variables win over the Keychain so CI and one-off runs can pass a key without storing it:
RANKWELL_ASC_ISSUER_ID, RANKWELL_ASC_KEY_ID and RANKWELL_ASC_KEY_PATH (path to the .p8 file).
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import NotConfigured

SERVICE = "rankwell"
_ISSUER, _KEY_ID, _PRIVATE_KEY = "asc_issuer_id", "asc_key_id", "asc_private_key"


@dataclass(frozen=True)
class AscKey:
    issuer_id: str
    key_id: str
    private_key: str

    def __repr__(self) -> str:  # never print the key
        return f"AscKey(issuer_id={self.issuer_id!r}, key_id={self.key_id!r}, private_key=<hidden>)"


def _keyring() -> Any:
    import keyring

    return keyring


def asc_key() -> AscKey:
    """The App Store Connect key, or NotConfigured with what to do."""
    issuer, kid, path = (os.environ.get(f"RANKWELL_ASC_{n}") for n in ("ISSUER_ID", "KEY_ID", "KEY_PATH"))
    if issuer and kid and path:
        return AscKey(issuer, kid, Path(path).expanduser().read_text())
    kr = _keyring()
    issuer = kr.get_password(SERVICE, _ISSUER)
    kid = kr.get_password(SERVICE, _KEY_ID)
    pk = kr.get_password(SERVICE, _PRIVATE_KEY)
    if not (issuer and kid and pk):
        raise NotConfigured(
            "No App Store Connect key. Run `rankwell init --asc-key <AuthKey_XXXX.p8> "
            "--issuer-id <id>` (the key id is read from the file name)."
        )
    return AscKey(issuer, kid, pk)


def has_asc_key() -> bool:
    try:
        asc_key()
    except NotConfigured:
        return False
    return True


def store_asc_key(issuer_id: str, key_id: str, private_key: str) -> None:
    if "BEGIN PRIVATE KEY" not in private_key:
        raise NotConfigured("That file is not an App Store Connect .p8 private key.")
    kr = _keyring()
    kr.set_password(SERVICE, _ISSUER, issuer_id.strip())
    kr.set_password(SERVICE, _KEY_ID, key_id.strip())
    kr.set_password(SERVICE, _PRIVATE_KEY, private_key.strip() + "\n")


def delete_asc_key() -> None:
    kr = _keyring()
    for name in (_ISSUER, _KEY_ID, _PRIVATE_KEY):
        with contextlib.suppress(Exception):  # missing entries are fine
            kr.delete_password(SERVICE, name)
