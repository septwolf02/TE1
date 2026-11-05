"""Windows-friendly local VOICEVOX diagnostics and sample generation."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import wave

from te.project.models import TTSSettings
from .voicevox import VoicevoxError, VoicevoxProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TE local VOICEVOX tools")
    parser.add_argument("command", choices=("check", "list", "sample"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50021)
    parser.add_argument("--voice", default="青山龍星", help="Speaker name or UUID")
    parser.add_argument("--style", help="Style name; defaults to ノーマル or first available")
    parser.add_argument("--text", default="今日の試合は、歴史に残る一戦です。")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--intonation", type=float, default=1.0)
    parser.add_argument("--volume", type=float, default=1.0)
    parser.add_argument("--sample-rate", type=int, default=24000)
    args = parser.parse_args(argv)
    settings = TTSSettings(provider="voicevox", voice_id=args.voice, style=args.style,
                           host=args.host, port=args.port, speaking_rate=args.speed,
                           pitch=args.pitch, intonation=args.intonation, volume=args.volume,
                           sample_rate=args.sample_rate)
    try:
        from te.project.models import Project
        Project._validate_tts_settings(settings)
        provider = VoicevoxProvider()
        if args.command == "check":
            print(f"VOICEVOX connected at http://{args.host}:{args.port} (version {provider.check_connection(settings)})")
        elif args.command == "list":
            for speaker in provider.list_voices(settings):
                print(f"{speaker['name']} [{speaker.get('speaker_uuid', 'unknown UUID')}]")
                for style in speaker.get("styles", []):
                    print(f"  {style['name']} (ID {style['id']})")
        else:
            if args.output is None:
                parser.error("sample requires --output")
            target = args.output.resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            metadata = provider.synthesize(args.text, settings, target)
            with wave.open(str(target), "rb") as stream:
                duration = stream.getnframes() / stream.getframerate()
            print(f"WAV: {target} ({duration:.2f}s)")
            print(f"Credit: {metadata['credit']} | License: {metadata['license_reference']}")
        return 0
    except (VoicevoxError, ValueError, OSError, wave.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
