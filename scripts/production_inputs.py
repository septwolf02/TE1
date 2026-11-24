"""Prepare and revalidate the approved Windows build inputs."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

FFMPEG = "ffmpeg-8.0.1-essentials_build"
FF_URL = f"https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/{FFMPEG}.zip"
FF_HASH = "e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673"
NOTO = "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/"
FONT = "NotoSansCJKjp-Regular.otf"
KEY = "release-public-key.pem"
FF_FILES = {
    "ffmpeg.exe": ("bin/ffmpeg.exe", "5af82a0d4fe2b9eae211b967332ea97edfc51c6b328ca35b827e73eac560dc0d"),
    "ffprobe.exe": ("bin/ffprobe.exe", "192a1d6899059765ac8c39764fc3148d4e6049955956dc2029f81f4bd6a8972d"),
    "FFmpeg-LICENSE.txt": ("LICENSE", "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"),
    "FFmpeg-README.txt": ("README.txt", "a0e976df3cf1d781264c41db8ee3421978c1278be92ed00edbc96337529670be"),
}
FONT_FILES = {
    FONT: (NOTO + "Sans/OTF/Japanese/" + FONT, "68a3fc98800b2a27b371f2fb79991daf3633bd89309d4ffaa6946fd587f375b5"),
    "OFL.txt": (NOTO + "LICENSE", "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"),
}
PINS = {name: digest for name, (_, digest) in (FF_FILES | FONT_FILES).items()}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified(path, expected):
    if not path.is_file():
        raise ValueError(f"Missing build input: {path}")
    if digest(path) != expected:
        raise ValueError(f"SHA-256 mismatch (unapproved binary, font or license): {path}")


def public_fingerprint(path, repository=None):
    if repository is not None and path.resolve().is_relative_to(repository.resolve()):
        raise ValueError("Keep the signing key files outside the repository.")
    lines = path.read_bytes().strip().splitlines()
    if (len(lines) < 3 or lines[0] != b"-----BEGIN PUBLIC KEY-----"
            or lines[-1] != b"-----END PUBLIC KEY-----"):
        raise ValueError("Expected an Ed25519 public PEM, never a private key.")
    der = base64.b64decode(b"".join(lines[1:-1]), validate=True)
    prefix = bytes.fromhex("302a300506032b6570032100")
    if len(der) != len(prefix) + 32 or not der.startswith(prefix):
        raise ValueError("Expected an Ed25519 public key.")
    return hashlib.sha256(der).hexdigest()


def fetch(url, target, expected):
    with urlopen(url, timeout=30) as response, target.open("wb") as stream:
        if not response.url.startswith("https://"):
            raise ValueError("Dependency download requires HTTPS.")
        remaining = 200 * 1024**2
        while chunk := response.read(1024**2):
            remaining -= len(chunk)
            if remaining < 0:
                raise ValueError("Dependency download exceeds 200 MiB.")
            stream.write(chunk)
    verified(target, expected)


def prepare(store, key, fingerprint, *, download=False, candidates=()):
    for name, expected in PINS.items():
        if (store / name).exists():
            verified(store / name, expected)
    with tempfile.TemporaryDirectory(dir=store.parent) as folder:
        stage = Path(folder)
        for name, expected in PINS.items():
            if (store / name).exists():
                continue
            relative_paths = [name]
            if name in FF_FILES:
                relative_paths.append(FF_FILES[name][0])
            elif name == "OFL.txt":
                relative_paths.append("LICENSE")
            for source in (directory / relative for directory in candidates for relative in relative_paths):
                if source.is_file() and digest(source) == expected:
                    shutil.copyfile(source, stage / name)
                    verified(stage / name, expected)
                    break
        missing_ff = [name for name in FF_FILES if not (store / name).exists()
                      and not (stage / name).exists()]
        missing_font = [name for name in FONT_FILES if not (store / name).exists()
                        and not (stage / name).exists()]
        if (missing_ff or missing_font) and not download:
            raise ValueError("Missing verified inputs: " + ", ".join(missing_ff + missing_font)
                             + ". Run preparation and approve the pinned downloads.")
        if missing_ff:
            archive = stage / "ffmpeg.zip"
            fetch(FF_URL, archive, FF_HASH)
            with ZipFile(archive) as package:
                for name in missing_ff:
                    member, expected = FF_FILES[name]
                    with package.open(f"{FFMPEG}/{member}") as source, (stage / name).open("wb") as target:
                        shutil.copyfileobj(source, target)
                    verified(stage / name, expected)
            archive.unlink()
        for name in missing_font:
            url, expected = FONT_FILES[name]
            fetch(url, stage / name, expected)
        shutil.copyfile(key, stage / KEY)
        if public_fingerprint(stage / KEY) != fingerprint:
            raise ValueError("Signing public key changed after approval.")
        store.mkdir(exist_ok=True)
        for path in stage.iterdir():
            os.replace(path, store / path.name)
        config = {"schema": 1, "public_fingerprint": fingerprint,
                  "sha256": {name: digest(store / name) for name in (*PINS, KEY)}}
        (stage / "build.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        os.replace(stage / "build.json", store / "build.json")


def validate(store):
    config = json.loads((store / "build.json").read_text(encoding="utf-8"))
    hashes = config["sha256"]
    if config["schema"] != 1 or hashes.keys() != {*PINS, KEY}:
        raise ValueError("Invalid local build configuration. Run preparation again.")
    for name, expected in PINS.items():
        if hashes[name] != expected:
            raise ValueError(f"Local build configuration disagrees with repository pins: {name}")
        verified(store / name, expected)
    verified(store / KEY, hashes[KEY])
    if public_fingerprint(store / KEY) != config["public_fingerprint"]:
        raise ValueError("Signing public key fingerprint changed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "fingerprint", "validate"))
    parser.add_argument("--store", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--fingerprint")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--candidate", type=Path, action="append", default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.action == "validate":
            validate(args.store)
            return
        fingerprint = public_fingerprint(args.key, root)
        if args.action == "fingerprint":
            print(fingerprint)
            return
        if fingerprint != args.fingerprint:
            raise ValueError("Signing public key fingerprint does not match owner approval.")
        prepare(args.store, args.key, fingerprint, download=args.download, candidates=args.candidate)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
