from __future__ import annotations

import argparse
import sys

from .cli import _add_common_options, _run_chat, add_chat_options


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="whatsapp-chat",
        description="Open the WhatsApp Reader terminal chat frontend.",
    )
    _add_common_options(parser)
    add_chat_options(parser)

    try:
        return _run_chat(parser.parse_args(args))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
