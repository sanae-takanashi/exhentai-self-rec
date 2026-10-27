"""Double-click entry point; the UI does not own the background service lifetime."""
from scripts.launcher import main


if __name__ == "__main__":
    raise SystemExit(main())
