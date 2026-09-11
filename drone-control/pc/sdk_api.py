"""CLI for direct DJI SDK GET/LISTEN/SET/CALL diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from drone_nav.config import load_config
from drone_nav.sdk_api import SdkApiClient


def main() -> int:
    # Windows terminals launched through automation can inherit cp1252 even
    # on a Korean system. Keep semantic Korean messages printable.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Directly query or call DJI KeyManager through the phone"
    )
    parser.add_argument("method", choices=("get", "listen", "set", "call", "action", "help"))
    parser.add_argument("module", nargs="?")
    parser.add_argument("key", nargs="?")
    parser.add_argument("parameter", nargs="?")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.local.json")))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=9997)
    parser.add_argument("--arm-token")
    args = parser.parse_args()

    config = load_config(args.config)
    host = args.host or config.network.host
    client = SdkApiClient(host, args.port)
    method = args.method.upper()
    if method == "LISTEN":
        try:
            for result in client.listen(args.module, args.key):
                print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        except KeyboardInterrupt:
            return 0
        return 0

    result = client.request(
        method,
        args.module,
        args.key,
        parameter=args.parameter,
        arm_token=args.arm_token,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
