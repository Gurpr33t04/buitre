import json
import sqlite3
import threading
from typing import Any, Iterator

from buitre.crawler.frontier import FrontierItem


SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier (
    url TEXT PRIMARY KEY,
    depth INTEGER NOT NULL,
    parent TEXT,
    score REAL NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    via TEXT NOT NULL DEFAULT 'link',
    template TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    final_url TEXT,
    status INTEGER,
    ok INTEGER NOT NULL,
    error TEXT,
    page_type TEXT,
    simhash TEXT,
    duplicate_of TEXT,
    record TEXT
);
"""


class CrawlStore:
    """
    SQLite persistence for a crawl: the pending queue and every finished
    page. Lets a crawl be stopped (Ctrl-C, crash, reboot) and resumed
    where it left off, and keeps results on disk rather than in memory.

    Only the crawler's coordinating thread writes to it; a lock still
    guards every operation.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.commit()
            self._db.close()

    # ------------------------------------------------------------ frontier

    def add_pending(self, item: FrontierItem) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO frontier "
                "(url, depth, parent, score, retries, via, template) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    item.url, item.depth, item.parent, item.score,
                    item.retries, item.via, item.template,
                ),
            )
            self._db.commit()

    def remove_pending(self, url: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM frontier WHERE url = ?", (url,))
            self._db.commit()

    def pending(self) -> list[FrontierItem]:
        with self._lock:
            rows = self._db.execute(
                "SELECT url, depth, parent, score, retries, via, template "
                "FROM frontier"
            ).fetchall()

        return [
            FrontierItem(
                url=r[0], depth=r[1], parent=r[2], score=r[3],
                retries=r[4], via=r[5], template=r[6],
            )
            for r in rows
        ]

    # --------------------------------------------------------------- pages

    def save_page(
        self,
        url: str,
        *,
        final_url: str | None,
        status: int | None,
        ok: bool,
        error: str | None = None,
        page_type: str | None = None,
        simhash: int | None = None,
        duplicate_of: str | None = None,
        record: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO pages "
                "(url, final_url, status, ok, error, page_type, simhash, "
                "duplicate_of, record) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    url, final_url, status, 1 if ok else 0, error,
                    page_type,
                    None if simhash is None else f"{simhash:016x}",
                    duplicate_of,
                    json.dumps(record, ensure_ascii=False) if record else None,
                ),
            )
            self._db.execute("DELETE FROM frontier WHERE url = ?", (url,))
            self._db.commit()

    def done_urls(self) -> set[str]:
        with self._lock:
            return {
                r[0] for r in self._db.execute("SELECT url FROM pages")
            }

    def final_urls(self) -> set[str]:
        with self._lock:
            return {
                r[0]
                for r in self._db.execute(
                    "SELECT final_url FROM pages "
                    "WHERE ok = 1 AND duplicate_of IS NULL "
                    "AND final_url IS NOT NULL"
                )
            }

    def simhashes(self) -> list[tuple[int, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT simhash, url FROM pages "
                "WHERE ok = 1 AND simhash IS NOT NULL "
                "AND duplicate_of IS NULL"
            ).fetchall()

        return [(int(r[0], 16), r[1]) for r in rows]

    def ok_count(self) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM pages "
                "WHERE ok = 1 AND duplicate_of IS NULL "
                "AND record IS NOT NULL"
            ).fetchone()[0]

    def iter_records(self) -> Iterator[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT record FROM pages WHERE record IS NOT NULL"
            ).fetchall()

        for (record,) in rows:
            yield json.loads(record)
