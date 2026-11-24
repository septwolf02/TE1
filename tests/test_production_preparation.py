import base64
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "production_inputs", Path(__file__).resolve().parents[1] / "scripts" / "production_inputs.py"
)
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


def pem(value):
    der = bytes.fromhex("302a300506032b6570032100") + bytes([value]) * 32
    return b"-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der) + b"\n-----END PUBLIC KEY-----\n"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    candidate = tmp_path / "distributions"
    candidate.mkdir()
    pins = {}
    for name in inputs.PINS:
        path = candidate / name
        path.write_bytes(name.encode())
        pins[name] = inputs.digest(path)
    monkeypatch.setattr(inputs, "PINS", pins)
    key = tmp_path / "owner.pem"
    key.write_bytes(pem(1))
    fingerprint = inputs.public_fingerprint(key, tmp_path / "repo")
    return tmp_path / "vendor", candidate, key, fingerprint


def test_preparation_missing_inputs_leaves_no_approved_config(setup):
    store, source, key, fingerprint = setup
    (source / "ffprobe.exe").unlink()
    with pytest.raises(ValueError, match="Missing verified inputs: ffprobe.exe"):
        inputs.prepare(store, key, fingerprint, candidates=[source])
    assert not store.exists()


@pytest.mark.parametrize("name", ["ffmpeg.exe", "OFL.txt", "FFmpeg-LICENSE.txt"])
def test_unapproved_candidate_is_not_accepted(setup, name):
    store, source, key, fingerprint = setup
    (source / name).write_text("unknown distribution")
    with pytest.raises(ValueError, match="Missing verified inputs"):
        inputs.prepare(store, key, fingerprint, candidates=[source])


def test_build_rechecks_inputs_and_rejects_rewritten_local_hash(setup):
    store, source, key, fingerprint = setup
    inputs.prepare(store, key, fingerprint, candidates=[source])
    inputs.validate(store)
    (store / "ffmpeg.exe").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        inputs.validate(store)
    config_path = store / "build.json"
    config = json.loads(config_path.read_text())
    config["sha256"]["ffmpeg.exe"] = inputs.digest(store / "ffmpeg.exe")
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="repository pins"):
        inputs.validate(store)


def test_public_key_type_location_and_owner_fingerprint(setup):
    store, source, key, fingerprint = setup
    assert fingerprint == hashlib.sha256(base64.b64decode(pem(1).splitlines()[1])).hexdigest()
    with pytest.raises(ValueError, match="outside the repository"):
        inputs.public_fingerprint(key, key.parent)
    key.write_bytes(pem(1).replace(b"PUBLIC KEY", b"PRIVATE KEY"))
    with pytest.raises(ValueError, match="never a private key"):
        inputs.public_fingerprint(key, key.parent / "repo")
    key.write_bytes(pem(2))
    with pytest.raises(ValueError, match="after approval"):
        inputs.prepare(store, key, fingerprint, candidates=[source])
    assert not store.exists()


def test_download_with_wrong_hash_never_approves_build(setup, monkeypatch):
    store, source, key, fingerprint = setup
    (source / "ffprobe.exe").unlink()

    class Response(BytesIO):
        url = inputs.FF_URL

    monkeypatch.setattr(inputs, "urlopen", lambda *args, **kwargs: Response(b"unverified archive"))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        inputs.prepare(store, key, fingerprint, candidates=[source], download=True)
    assert not store.exists()
