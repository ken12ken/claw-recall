#!/usr/bin/env python3
"""
Claw Recall — Real-Time File Watcher

Watches OpenClaw session directories for new/modified .jsonl files
and indexes them automatically using watchdog + inotify.

Usage:
    python3 watcher.py                     # Run in foreground
    systemctl start claw-recall-watcher    # Run as service
"""

import sys
import time
import sqlite3
import threading
import logging
from pathlib import Path
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent

sys.path.insert(0, str(Path(__file__).parent))
from index import index_session_file, DB_PATH

# Directories to watch
WATCH_DIRS = [
    Path.home() / ".openclaw" / "agents",
    Path.home() / ".openclaw" / "agents-archive",
]

# Debounce settings
DEBOUNCE_SECONDS = 5  # Wait 5s after last change before indexing
EMBEDDING_ON_WATCH = False  # Don't generate embeddings on watch (too slow/expensive)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [watcher] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger("watcher")


class SessionFileHandler(FileSystemEventHandler):
    """Handles .jsonl file changes with debounced indexing."""

    def __init__(self):
        super().__init__()
        self._pending = {}  # path -> timer
        self._lock = threading.Lock()
        self._stats = {"indexed": 0, "skipped": 0, "errors": 0}

    def _should_handle(self, path: str) -> bool:
        """Only handle .jsonl files, skip subagents."""
        return (path.endswith('.jsonl')
                and '/subagents/' not in path
                and '.deleted.' not in path)

    def on_created(self, event):
        if not event.is_directory and self._should_handle(event.src_path):
            self._schedule_index(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and self._should_handle(event.src_path):
            self._schedule_index(event.src_path)

    def _schedule_index(self, path: str):
        """Schedule indexing with debounce — waits for file to stop being written."""
        with self._lock:
            if path in self._pending:
                self._pending[path].cancel()
            timer = threading.Timer(DEBOUNCE_SECONDS, self._do_index, args=[path])
            timer.daemon = True
            timer.start()
            self._pending[path] = timer

    def _do_index(self, path: str):
        """Actually index the file."""
        with self._lock:
            self._pending.pop(path, None)

        filepath = Path(path)
        if not filepath.exists():
            return

        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("PRAGMA journal_mode=WAL")
            result = index_session_file(filepath, conn, generate_embeds=EMBEDDING_ON_WATCH)
            conn.close()

            if result['status'] == 'indexed':
                self._stats["indexed"] += 1
                log.info(f"Indexed: {filepath.name} ({result['messages']} msgs)")
            else:
                self._stats["skipped"] += 1
        except Exception as e:
            self._stats["errors"] += 1
            log.error(f"Error indexing {filepath.name}: {e}")

    @property
    def stats(self):
        return dict(self._stats)


def main():
    handler = SessionFileHandler()
    observer = Observer()

    watched = 0
    for watch_dir in WATCH_DIRS:
        if watch_dir.exists():
            observer.schedule(handler, str(watch_dir), recursive=True)
            watched += 1
            log.info(f"Watching: {watch_dir}")
        else:
            log.warning(f"Directory not found, skipping: {watch_dir}")

    if watched == 0:
        log.error("No directories to watch!")
        sys.exit(1)

    observer.start()
    log.info(f"Claw Recall watcher started — monitoring {watched} directories")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        observer.stop()
        log.info(f"Watcher stopped. Stats: {handler.stats}")

    observer.join()


if __name__ == "__main__":
    main()
