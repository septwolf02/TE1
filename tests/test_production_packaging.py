"""Cloud checks for packaging contracts; Windows installer still needs a Windows VM."""
import base64
from hashlib import sha256
import io
import json
from pathlib import Path
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from te import runtime
from te.render.service import RenderService, RenderSettings
from te.studio.edit_lock import ProjectEditLock
from te.studio import production_update as update
from te.studio.service import StudioService, STUDIO_VERSION


def test_bundled_tools_and_font_take_priority_without_changing_explicit_font(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    bin_dir = tmp_path / "resources" / "bin"
    font_dir = tmp_path / "resources" / "fonts"
    bin_dir.mkdir(parents=True)
    font_dir.mkdir(parents=True)
    ffmpeg = bin_dir / "ffmpeg"
    ffprobe = bin_dir / "ffprobe"
    ffmpeg.write_bytes(b"fake")
    ffprobe.write_bytes(b"fake")
    font = font_dir / "NotoSansCJKjp-Regular.otf"
    font.write_bytes(b"font")
    monkeypatch.setattr(runtime.os, "name", "posix")
    assert runtime.tool_path("ffmpeg") == str(ffmpeg)
    assert runtime.tool_path("ffprobe") == str(ffprobe)
    assert RenderService._resolve_font(RenderSettings()) == ("Noto Sans CJK JP", font.resolve())
    chosen = tmp_path / "custom.otf"
    chosen.write_bytes(b"custom")
    assert RenderService._resolve_font(RenderSettings(font_path=str(chosen)))[1] == chosen.resolve()


def test_project_edit_lock_rejects_second_editor_and_releases(tmp_path):
    first = ProjectEditLock(tmp_path)
    try:
        with pytest.raises(ValueError, match="đang mở"):
            ProjectEditLock(tmp_path)
    finally:
        first.close()
    second = ProjectEditLock(tmp_path)
    second.close()


def test_production_settings_are_copied_once_without_touching_development(tmp_path, monkeypatch):
    from te.studio import service as studio_service
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(studio_service, "production_build", lambda: True)
    legacy = tmp_path / "TEStudio" / "settings.json"
    legacy.parent.mkdir()
    original = {"version":STUDIO_VERSION,"projects_root":"D:/TE-Projects",
                "output_root":"D:/TE-Output", "profiles":{}, "profile_aliases":{},
                "render_encoder":"auto", "youtube_oauth_client_path":"private.json"}
    legacy.write_text(json.dumps(original), encoding="utf-8")
    production = StudioService()
    assert production.config_path == tmp_path / "TEStudio" / "production" / "settings.json"
    assert production.settings["projects_root"] == "D:/TE-Projects"
    assert "youtube_oauth_client_path" not in production.settings
    production.settings["voicevox_setup_seen"] = True
    production.save_settings()
    assert json.loads(legacy.read_text(encoding="utf-8")) == original
    original["projects_root"] = "D:/Development-Only"
    legacy.write_text(json.dumps(original), encoding="utf-8")
    assert StudioService().settings["projects_root"] == "D:/TE-Projects"


def signed_release():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    installer = b"test installer bytes"
    manifest = {"schema":1,"release_kind":"production","version":"1.0.0",
                "installer":"TE-Studio-Setup-1.0.0.exe","sha256":sha256(installer).hexdigest(),
                "bytes":len(installer),"notes":"Đã kiểm thử trên Windows."}
    encoded = json.dumps(manifest, ensure_ascii=False).encode()
    return public, encoded, base64.b64encode(private.sign(encoded)), manifest, installer


def test_signed_production_release_ignores_unapproved_and_prereleases():
    public, encoded, signature, manifest, installer = signed_release()
    assert update.verify_release_manifest(encoded, signature, public) == manifest
    with pytest.raises(Exception):
        update.verify_release_manifest(encoded.replace(b"1.0.0", b"2.0.0"), signature, public)
    prefix = "https://github.com/septwolf02/TE1/releases/download/v1.0.0/"
    assets = [{"name":name,"browser_download_url":prefix+name,
               "size":len(installer) if name.endswith(".exe") else 1}
              for name in ("release.json", "release.json.sig", manifest["installer"])]
    releases = [dict(tag_name="v1.1.0", draft=False, prerelease=True, assets=assets),
                dict(tag_name="v1.0.0", draft=False, prerelease=False, assets=assets)]
    def read(url, _limit):
        if url == update.API_URL:
            return json.dumps(releases).encode()
        if url.endswith("release.json"):
            return encoded
        if url.endswith("release.json.sig"):
            return signature
        raise AssertionError(url)
    found = update.check_for_update(public, current_version="1.0.0rc1", read_url=read)
    assert found and found.version == "1.0.0" and found.installer_size == len(installer)
    assert update.check_for_update(public, current_version="1.0.0", read_url=read) is None
    releases[1]["draft"] = True
    assert update.check_for_update(public, current_version="1.0.0rc1", read_url=read) is None


def test_installer_hash_and_size_are_checked_before_atomic_publish(tmp_path, monkeypatch):
    _, _, _, manifest, data = signed_release()
    release = update.ProductionRelease("1.0.0", "notes",
        "https://github.com/septwolf02/TE1/releases/download/v1.0.0/TE-Studio-Setup-1.0.0.exe",
        manifest["installer"], len(data), manifest["sha256"])
    class Response(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.close()
    monkeypatch.setattr(update, "urlopen", lambda *_args, **_kwargs: Response(data))
    assert update.download_verified_installer(release, tmp_path).read_bytes() == data
    (tmp_path / release.installer_name).unlink()
    monkeypatch.setattr(update, "urlopen", lambda *_args, **_kwargs: Response(b"wrong"))
    with pytest.raises(ValueError, match="khớp"):
        update.download_verified_installer(release, tmp_path)
    assert not list(tmp_path.iterdir())
