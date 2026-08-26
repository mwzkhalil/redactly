"""Delete the demo database so the next start re-seeds fixtures from scratch.

Useful before recording: the audit trail is append-only by design, so the only
way to get an empty one is to start over. The master key is left in place, since
regenerating it would orphan nothing but costs a re-encrypt for no benefit.

    uv run python scripts/reset_demo.py

The service has to be stopped first — SQLite holds the file open while it runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402


def main() -> int:
    database = get_settings().database_path
    # The write-ahead log and shared-memory file have to go too, or SQLite will
    # recover a "deleted" database from them on the next open.
    targets = [database, *(database.with_name(database.name + suffix) for suffix in ("-wal", "-shm"))]

    present = [path for path in targets if path.exists()]
    if not present:
        print(f"Nothing to remove; {database} does not exist. The next start will seed fixtures.")
        return 0

    for path in present:
        try:
            path.unlink()
        except PermissionError:
            print(
                f"Could not delete {path}.\n"
                "The policy service is probably still running — stop it and run this again.",
                file=sys.stderr,
            )
            return 1
        print(f"removed {path.name}")

    print("\nClean. Start the service and the three fixtures will be re-scanned and re-seeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
