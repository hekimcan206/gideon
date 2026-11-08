"""Command line: ``python -m gideon {serve,export-schema,fixtures,bench}``."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gideon", description="GIDEON voice assistant backend")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "serve", help="run the pipeline and the WebSocket server (settings from GIDEON_* env vars)"
    )
    schema = sub.add_parser("export-schema", help="write the protocol JSON schema")
    schema.add_argument("--out", type=Path, default=Path("../protocol/schema.json"))
    fixtures = sub.add_parser("fixtures", help="generate the synthetic WAV fixtures with edge-tts")
    fixtures.add_argument("--out", type=Path, default=Path("../fixtures"))
    bench = sub.add_parser("bench", help="measure per-stage latency on the WAV fixtures")
    bench.add_argument("--fixtures", type=Path, default=Path("../fixtures"))
    bench.add_argument("--out", type=Path, default=Path("../bench-results"))
    bench.add_argument("--runs", type=int, default=10)
    bench.add_argument("--label", default="default")
    stt = sub.add_parser(
        "bench-stt", help="STT alone: latency and accuracy per model/threads/language strategy"
    )
    stt.add_argument("--fixtures", type=Path, default=Path("../fixtures"))
    stt.add_argument("--out", type=Path, default=Path("../bench-results"))
    stt.add_argument("--runs", type=int, default=5)
    stt.add_argument("--models", default="tiny,base")
    args = parser.parse_args(argv)

    if args.command == "export-schema":
        from gideon.protocol import json_schema

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    elif args.command == "fixtures":
        from gideon import fixtures as fx

        fx.main(args.out)
    elif args.command == "bench-stt":
        from gideon import bench as b
        from gideon import logs

        logs.configure("WARNING")
        asyncio.run(b.run_stt(args.fixtures, args.out, args.runs, args.models.split(",")))
    elif args.command == "bench":
        from gideon import bench as b
        from gideon import logs

        logs.configure("WARNING")
        b.main(args.fixtures, args.out, args.runs, args.label)
    else:
        from gideon import app, logs
        from gideon.config import Settings

        settings = Settings.from_env()
        logs.configure(settings.log_level)
        asyncio.run(app.serve(settings))


if __name__ == "__main__":
    main()
