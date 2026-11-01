"""Service layer for Niruvi application business logic.

Provides decoupled business logic services that can be injected
into UI components and other parts of the application.
"""

from __future__ import annotations

import logging

from niruvi.core.manifest import Manifest, ManifestError
from niruvi.core.scanner import SUSPICIOUS_EXTENSIONS, SUSPICIOUS_PATTERNS, scan_directory, scan_file
from niruvi.core.verification import verify_gpg_signature, verify_sha256
from niruvi.repositories.installation_record import InstallationRecordRepository
from niruvi.utils.http import _create_ssl_context, fetch_json
from niruvi.utils.integrity import sign_data, verify_data
from niruvi.utils.sound_manager import play as play_sound

logger = logging.getLogger(__name__)

__all__ = [
    "SUSPICIOUS_EXTENSIONS",
    "SUSPICIOUS_PATTERNS",
    "InstallationRecordRepository",
    "Manifest",
    "ManifestError",
    "_create_ssl_context",
    "fetch_json",
    "play_sound",
    "scan_directory",
    "scan_file",
    "sign_data",
    "verify_data",
    "verify_gpg_signature",
    "verify_sha256",
]
