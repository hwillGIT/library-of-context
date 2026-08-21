from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1])
    connection = sqlite3.connect(path)
    try:
        version_row = connection.execute(
            "SELECT value FROM cache_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version_row is None or int(version_row[0]) != 2:
            print("schema-2 reader rejects this database", file=sys.stderr)
            return 2
        payload = {
            "records": connection.execute("SELECT COUNT(*) FROM records").fetchone()[0],
            "events": connection.execute(
                "SELECT COUNT(*) FROM thread_events"
            ).fetchone()[0],
            "outbox": connection.execute(
                "SELECT COUNT(*) FROM context_outbox"
            ).fetchone()[0],
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
