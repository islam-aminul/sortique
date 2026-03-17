"""Allow running Sortique with `python -m sortique`."""

import sys


def main() -> None:
    if len(sys.argv) > 1:
        from sortique.cli import build_parser, dispatch_cli

        args = build_parser().parse_args()
        sys.exit(dispatch_cli(args))
    else:
        from sortique.app import main as gui_main

        gui_main()


if __name__ == "__main__":
    main()
