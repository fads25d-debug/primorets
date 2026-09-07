import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .core import Record


def default_data_dir():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Primorets"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "primorets"


class Catalog:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS satellites (
            id TEXT PRIMARY KEY, name TEXT, kind TEXT, payload TEXT, source TEXT,
            demo INTEGER, epoch TEXT, imported_at TEXT)""")

    def import_records(self, records):
        added = skipped = 0
        with self.db:
            for record in records:
                p = record.properties()
                key = f"{'demo' if record.demo else 'real'}:{p['norad']}"
                epoch = p["epoch"].isoformat()
                previous = self.db.execute("SELECT epoch FROM satellites WHERE id=?", (key,)).fetchone()
                if previous and previous[0] >= epoch:
                    skipped += 1
                    continue
                self.db.execute("INSERT OR REPLACE INTO satellites VALUES (?,?,?,?,?,?,?,?)",
                                (key, record.name, record.kind, json.dumps(record.payload, ensure_ascii=False),
                                 record.source, int(record.demo), epoch, datetime.now(timezone.utc).isoformat()))
                added += 1
        return added, skipped

    def all(self):
        return [(row[0], Record(row[1], row[2], json.loads(row[3]), row[4], bool(row[5])))
                for row in self.db.execute("SELECT id,name,kind,payload,source,demo FROM satellites ORDER BY name")]

    def close(self):
        self.db.close()

