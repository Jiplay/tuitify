import sys

from src.tui.tui import Tuitify


def main() -> int:
    """Run the app, and never greet the user with a raw traceback."""
    try:
        Tuitify().run()
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # e.g. an unreadable config, a missing terminal
        print(f"tuitify: could not start: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
