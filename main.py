"""Minimal ISABELLA bootstrap shared by GUI and CLI modes."""

import argparse

from Isabella.Runtime import ApplicationRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description="I.S.A.B.E.L.L.A. local assistant")
    parser.add_argument("--cli", action="store_true", help="use the terminal interface")
    args = parser.parse_args()
    runtime = ApplicationRuntime.from_config(mode="cli" if args.cli else "gui")
    try:
        if not runtime.start():
            raise SystemExit(1)
        raise SystemExit(runtime.wait())
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    main()
