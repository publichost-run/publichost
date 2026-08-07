"""SSH host key management."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import asyncssh

logger = logging.getLogger(__name__)


def ensure_host_key(path: str) -> str:
    """Return path to the SSH host key, generating a new one on first run."""
    key_path = Path(path)
    if not key_path.exists():
        logger.info("Generating new SSH host key at %s", key_path)
        key = asyncssh.generate_private_key("ssh-ed25519")
        data = key.export_private_key().decode()
        # Create the file already restricted to 0600 so the private key is
        # never briefly world-readable. O_EXCL avoids clobbering a key that
        # appeared in the meantime (TOCTOU with the exists() check above).
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
    return str(key_path)
