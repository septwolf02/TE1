"""Sign an already Windows-verified installer; never uploads or publishes a release."""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from packaging.version import Version

from te.studio.version import APP_VERSION


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = Version(args.version)
    if version.is_prerelease or version.is_devrelease or args.version != APP_VERSION or \
            not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        raise ValueError("Only the matching, stable Production version can be signed.")
    if args.installer.name != f"TE-Studio-Setup-{args.version}.exe":
        raise ValueError("Installer filename does not match the version.")
    key = load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("An Ed25519 private key is required.")
    notes = args.notes.read_text(encoding="utf-8").strip()
    if not notes or len(notes) > 10000:
        raise ValueError("Release notes are required and limited to 10,000 characters.")
    digest = sha256()
    with args.installer.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    payload = {"schema": 1, "release_kind": "production", "version": args.version,
               "installer": args.installer.name, "sha256": digest.hexdigest(),
               "bytes": args.installer.stat().st_size, "notes": notes}
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "release.json").write_bytes(encoded)
    (args.output / "release.json.sig").write_bytes(base64.b64encode(key.sign(encoded)) + b"\n")


if __name__ == "__main__":
    main()
