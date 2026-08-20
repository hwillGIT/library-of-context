from __future__ import annotations

from .cli_commands import execute_command
from .cli_parser import build_parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return execute_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
