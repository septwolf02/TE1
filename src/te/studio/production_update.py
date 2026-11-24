"""Signed, opt-in Production installer updates from GitHub Releases."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from packaging.version import Version, InvalidVersion

from te.runtime import bundled_resource, production_build
from .version import APP_VERSION, RELEASE_REPOSITORY

API_URL = f"https://api.github.com/repos/{RELEASE_REPOSITORY}/releases?per_page=20"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class ProductionRelease:
    version: str
    notes: str
    installer_url: str
    installer_name: str
    installer_size: int
    installer_sha256: str


def _read_url(url: str, limit: int) -> bytes:
    if urlparse(url).scheme != "https":
        raise ValueError("Bản cập nhật phải được tải qua HTTPS.")
    request = Request(url, headers={"Accept": "application/vnd.github+json",
                                    "User-Agent": "TE-Studio-Production-Updater"})
    with urlopen(request, timeout=15) as response:
        value = response.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Metadata bản cập nhật vượt giới hạn.")
    return value


def verify_release_manifest(manifest_bytes: bytes, signature_bytes: bytes,
                            public_key_pem: bytes) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if len(manifest_bytes) > MAX_MANIFEST_BYTES or len(signature_bytes) > 256:
        raise ValueError("Manifest hoặc chữ ký vượt giới hạn.")
    key = load_pem_public_key(public_key_pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Khóa Production phải là Ed25519.")
    try:
        key.verify(base64.b64decode(signature_bytes.strip(), validate=True), manifest_bytes)
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise ValueError("Chữ ký Production Release không hợp lệ.") from exc
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or set(manifest) != {
            "schema", "release_kind", "version", "installer", "sha256", "bytes", "notes"} or \
            manifest["schema"] != 1 or manifest["release_kind"] != "production" or \
            not isinstance(manifest["version"], str) or \
            not isinstance(manifest["installer"], str) or \
            not isinstance(manifest["sha256"], str) or \
            not isinstance(manifest["notes"], str) or len(manifest["notes"]) > 10000 or \
            type(manifest["bytes"]) is not int or not 0 < manifest["bytes"] <= MAX_INSTALLER_BYTES:
        raise ValueError("Manifest Production không hợp lệ.")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest["version"]) or \
            manifest["installer"] != f"TE-Studio-Setup-{manifest['version']}.exe" or \
            len(manifest["sha256"]) != 64 or \
            any(char not in "0123456789abcdef" for char in manifest["sha256"]):
        raise ValueError("Tên installer hoặc SHA-256 không hợp lệ.")
    try:
        parsed = Version(manifest["version"])
    except InvalidVersion as exc:
        raise ValueError("Phiên bản Production không hợp lệ.") from exc
    if parsed.is_prerelease or parsed.is_devrelease:
        raise ValueError("Manifest Production không được ký cho prerelease.")
    return manifest


def check_for_update(public_key_pem: bytes, *, current_version: str = APP_VERSION,
                     read_url=_read_url) -> ProductionRelease | None:
    releases = json.loads(read_url(API_URL, 512 * 1024))
    if not isinstance(releases, list):
        raise ValueError("GitHub Releases trả về dữ liệu không hợp lệ.")
    best = None
    prefix = f"https://github.com/{RELEASE_REPOSITORY}/releases/download/"
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            continue
        assets = {item.get("name"): item for item in release.get("assets", [])
                  if isinstance(item, dict) and isinstance(item.get("name"), str)}
        manifest_asset, signature_asset = assets.get("release.json"), assets.get("release.json.sig")
        if not manifest_asset or not signature_asset:
            continue
        def asset_url(item):
            url = item.get("browser_download_url", "")
            if not isinstance(url, str) or not url.startswith(prefix + tag + "/"):
                raise ValueError("Asset Release không thuộc repository/tag đã chọn.")
            return url
        manifest_bytes = read_url(asset_url(manifest_asset), MAX_MANIFEST_BYTES)
        signature_bytes = read_url(asset_url(signature_asset), 256)
        manifest = verify_release_manifest(manifest_bytes, signature_bytes, public_key_pem)
        if tag != "v" + manifest["version"] or Version(manifest["version"]) <= Version(current_version):
            continue
        installer = assets.get(manifest["installer"])
        if not installer or installer.get("size") != manifest["bytes"]:
            raise ValueError("Installer không khớp manifest đã ký.")
        candidate = ProductionRelease(manifest["version"], manifest["notes"],
            asset_url(installer), manifest["installer"], manifest["bytes"], manifest["sha256"])
        if best is None or Version(candidate.version) > Version(best.version):
            best = candidate
    return best


def trusted_public_key() -> bytes:
    path = bundled_resource("release", "release-public-key.pem")
    if not production_build() or path is None:
        raise ValueError("Chỉ bản Production đã đóng gói mới có khóa cập nhật tin cậy.")
    return path.read_bytes()


def download_verified_installer(release: ProductionRelease, destination: Path) -> Path:
    prefix = f"https://github.com/{RELEASE_REPOSITORY}/releases/download/v{release.version}/"
    if not release.installer_url.startswith(prefix) or \
            release.installer_name != f"TE-Studio-Setup-{release.version}.exe":
        raise ValueError("Địa chỉ installer không hợp lệ.")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / release.installer_name
    partial = target.with_suffix(".part")
    digest, size = sha256(), 0
    try:
        request = Request(release.installer_url, headers={"User-Agent":"TE-Studio-Production-Updater"})
        with urlopen(request, timeout=30) as response, partial.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > release.installer_size or size > MAX_INSTALLER_BYTES:
                    raise ValueError("Installer vượt kích thước đã ký.")
                digest.update(chunk)
                stream.write(chunk)
        if size != release.installer_size or digest.hexdigest() != release.installer_sha256:
            raise ValueError("Installer không khớp chữ ký manifest và SHA-256.")
        os.replace(partial, target)
        return target
    finally:
        partial.unlink(missing_ok=True)
