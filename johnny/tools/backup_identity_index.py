import argparse
import ctypes
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional


FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def sha256_path(path: str) -> tuple[Optional[str], Optional[str]]:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), None
    except Exception as exc:
        return None, str(exc)


def build_metadata_signature(path: str, size: int, modified_time: str) -> str:
    payload = f"{path}|{size}|{modified_time}"
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime_dwLowDateTime", ctypes.c_uint32),
        ("ftCreationTime_dwHighDateTime", ctypes.c_uint32),
        ("ftLastAccessTime_dwLowDateTime", ctypes.c_uint32),
        ("ftLastAccessTime_dwHighDateTime", ctypes.c_uint32),
        ("ftLastWriteTime_dwLowDateTime", ctypes.c_uint32),
        ("ftLastWriteTime_dwHighDateTime", ctypes.c_uint32),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


def get_windows_identity(path: str) -> Dict[str, str]:
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            ctypes.c_wchar_p(path),
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == -1 or handle == 0:
            raise OSError("CreateFileW failed")
        info = BY_HANDLE_FILE_INFORMATION()
        ok = kernel32.GetFileInformationByHandle(handle, ctypes.byref(info))
        kernel32.CloseHandle(handle)
        if not ok:
            raise OSError("GetFileInformationByHandle failed")
        volume = f"{info.dwVolumeSerialNumber:08x}"
        file_index = f"{info.nFileIndexHigh:08x}{info.nFileIndexLow:08x}"
        return {
            "volume_serial_number": volume,
            "windows_file_id": file_index,
            "identity_source": "windows_file_id",
        }
    except Exception:
        stat = os.stat(path)
        stat_ino = getattr(stat, "st_ino", 0)
        stat_dev = getattr(stat, "st_dev", 0)
        if stat_ino:
            return {
                "volume_serial_number": str(stat_dev or "fallback"),
                "windows_file_id": str(stat_ino),
                "identity_source": "stat_inode",
            }
        fallback = hashlib.sha1(f"{stat.st_ctime_ns}|{stat.st_dev}".encode("utf-8")).hexdigest()
        return {
            "volume_serial_number": "fallback",
            "windows_file_id": fallback,
            "identity_source": "ctime_fallback",
        }


def file_key_from_identity(volume_serial_number: str, windows_file_id: str) -> str:
    return hashlib.sha1(f"{volume_serial_number}|{windows_file_id}".encode("utf-8")).hexdigest()


@dataclass
class FileObservation:
    file_key: str
    volume_serial_number: str
    windows_file_id: str
    identity_source: str
    original_path: str
    current_path: str
    file_name: str
    extension: str
    size: int
    created_time: str
    modified_time: str
    first_seen_at: str
    last_seen_at: str
    scan_root: str
    is_active: int
    metadata_signature: str
    current_sha256: Optional[str]
    content_identity_status: str
    hash_error: Optional[str]


MASTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_key TEXT PRIMARY KEY,
    volume_serial_number TEXT NOT NULL,
    windows_file_id TEXT NOT NULL,
    identity_source TEXT NOT NULL,
    original_path TEXT NOT NULL,
    current_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_time TEXT NOT NULL,
    modified_time TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    scan_root TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    metadata_signature TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_scan_root ON files(scan_root);
"""


CURRENT_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS current_files (
    file_key TEXT PRIMARY KEY,
    volume_serial_number TEXT NOT NULL,
    windows_file_id TEXT NOT NULL,
    identity_source TEXT NOT NULL,
    original_path TEXT NOT NULL,
    current_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_time TEXT NOT NULL,
    modified_time TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    scan_root TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    metadata_signature TEXT NOT NULL,
    current_sha256 TEXT,
    content_identity_status TEXT NOT NULL DEFAULT 'not_hashed_yet'
);
CREATE INDEX IF NOT EXISTS idx_current_scan_root ON current_files(scan_root);
CREATE TABLE IF NOT EXISTS change_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,
    path_changed INTEGER NOT NULL DEFAULT 0,
    content_changed INTEGER NOT NULL DEFAULT 0,
    active_state_changed INTEGER NOT NULL DEFAULT 0,
    file_key TEXT NOT NULL,
    volume_serial_number TEXT NOT NULL,
    windows_file_id TEXT NOT NULL,
    identity_source TEXT NOT NULL,
    original_path TEXT NOT NULL,
    previous_path TEXT,
    current_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_time TEXT NOT NULL,
    modified_time TEXT NOT NULL,
    sha256 TEXT,
    previous_sha256 TEXT,
    scan_root TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    content_identity_status TEXT NOT NULL,
    last_error TEXT,
    exported_to_jsonl INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_change_events_export ON change_events(exported_to_jsonl, id);
CREATE TABLE IF NOT EXISTS pending_master_additions (
    file_key TEXT PRIMARY KEY,
    volume_serial_number TEXT NOT NULL,
    windows_file_id TEXT NOT NULL,
    identity_source TEXT NOT NULL,
    original_path TEXT NOT NULL,
    current_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_time TEXT NOT NULL,
    modified_time TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    scan_root TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    metadata_signature TEXT NOT NULL,
    mirrored_to_master INTEGER NOT NULL DEFAULT 0
);
"""


def ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def connect_sqlite(path: str) -> sqlite3.Connection:
    ensure_parent(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_master(conn: sqlite3.Connection) -> None:
    conn.executescript(MASTER_SCHEMA)
    conn.commit()


def init_current(conn: sqlite3.Connection) -> None:
    conn.executescript(CURRENT_SCHEMA)
    conn.commit()


def iterate_files(target_path: str, top_level_only: bool, exclude_paths: Optional[set[str]] = None) -> Iterable[str]:
    target_path = os.path.abspath(target_path)
    exclude_paths = exclude_paths or set()
    if os.path.isfile(target_path):
        if target_path not in exclude_paths:
            yield target_path
        return
    if top_level_only:
        for name in sorted(os.listdir(target_path)):
            path = os.path.join(target_path, name)
            if os.path.isfile(path) and os.path.abspath(path) not in exclude_paths:
                yield path
        return
    for root, dirs, files in os.walk(target_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file_name in sorted(files):
            if file_name.startswith("."):
                continue
            path = os.path.join(root, file_name)
            if os.path.abspath(path) not in exclude_paths:
                yield path


def observe_path(path: str, scan_root: str, should_hash: bool) -> Optional[FileObservation]:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    identity = get_windows_identity(path)
    volume = identity["volume_serial_number"]
    file_id = identity["windows_file_id"]
    file_key = file_key_from_identity(volume, file_id)
    now = utc_now()
    created_time = iso_from_ts(stat.st_ctime)
    modified_time = iso_from_ts(stat.st_mtime)
    file_name = os.path.basename(path)
    extension = os.path.splitext(file_name)[1].lower()
    current_hash, hash_error = sha256_path(path) if should_hash else (None, None)
    status = "hashed" if current_hash else ("hash_error" if hash_error else "not_hashed_yet")
    return FileObservation(
        file_key=file_key,
        volume_serial_number=volume,
        windows_file_id=file_id,
        identity_source=identity["identity_source"],
        original_path=path,
        current_path=path,
        file_name=file_name,
        extension=extension,
        size=stat.st_size,
        created_time=created_time,
        modified_time=modified_time,
        first_seen_at=now,
        last_seen_at=now,
        scan_root=scan_root,
        is_active=1,
        metadata_signature=build_metadata_signature(path, stat.st_size, modified_time),
        current_sha256=current_hash,
        content_identity_status=status,
        hash_error=hash_error,
    )


def current_rows_by_scan_root(conn: sqlite3.Connection, scan_root: str) -> Dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM current_files WHERE scan_root = ?", (scan_root,)).fetchall()
    return {row["file_key"]: row for row in rows}


def insert_master_row(conn: sqlite3.Connection, row: Dict[str, object]) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO files (
            file_key, volume_serial_number, windows_file_id, identity_source, original_path, current_path,
            file_name, extension, size, created_time, modified_time, first_seen_at, last_seen_at,
            scan_root, is_active, metadata_signature
        ) VALUES (
            :file_key, :volume_serial_number, :windows_file_id, :identity_source, :original_path, :current_path,
            :file_name, :extension, :size, :created_time, :modified_time, :first_seen_at, :last_seen_at,
            :scan_root, :is_active, :metadata_signature
        )
        """,
        row,
    )


def upsert_current_row(conn: sqlite3.Connection, row: Dict[str, object]) -> None:
    conn.execute(
        """
        INSERT INTO current_files (
            file_key, volume_serial_number, windows_file_id, identity_source, original_path, current_path,
            file_name, extension, size, created_time, modified_time, first_seen_at, last_seen_at,
            scan_root, is_active, metadata_signature, current_sha256, content_identity_status
        ) VALUES (
            :file_key, :volume_serial_number, :windows_file_id, :identity_source, :original_path, :current_path,
            :file_name, :extension, :size, :created_time, :modified_time, :first_seen_at, :last_seen_at,
            :scan_root, :is_active, :metadata_signature, :current_sha256, :content_identity_status
        )
        ON CONFLICT(file_key) DO UPDATE SET
            current_path=excluded.current_path,
            file_name=excluded.file_name,
            extension=excluded.extension,
            size=excluded.size,
            created_time=excluded.created_time,
            modified_time=excluded.modified_time,
            last_seen_at=excluded.last_seen_at,
            scan_root=excluded.scan_root,
            is_active=excluded.is_active,
            metadata_signature=excluded.metadata_signature,
            current_sha256=COALESCE(excluded.current_sha256, current_files.current_sha256),
            content_identity_status=excluded.content_identity_status
        """,
        row,
    )


def queue_master_addition(conn: sqlite3.Connection, row: Dict[str, object]) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO pending_master_additions (
            file_key, volume_serial_number, windows_file_id, identity_source, original_path, current_path,
            file_name, extension, size, created_time, modified_time, first_seen_at, last_seen_at,
            scan_root, is_active, metadata_signature, mirrored_to_master
        ) VALUES (
            :file_key, :volume_serial_number, :windows_file_id, :identity_source, :original_path, :current_path,
            :file_name, :extension, :size, :created_time, :modified_time, :first_seen_at, :last_seen_at,
            :scan_root, :is_active, :metadata_signature, 0
        )
        """,
        row,
    )


def insert_event(conn: sqlite3.Connection, event: Dict[str, object]) -> None:
    conn.execute(
        """
        INSERT INTO change_events (
            observed_at, stage, event_type, path_changed, content_changed, active_state_changed,
            file_key, volume_serial_number, windows_file_id, identity_source, original_path, previous_path,
            current_path, file_name, extension, size, created_time, modified_time, sha256, previous_sha256,
            scan_root, is_active, content_identity_status, last_error, exported_to_jsonl
        ) VALUES (
            :observed_at, :stage, :event_type, :path_changed, :content_changed, :active_state_changed,
            :file_key, :volume_serial_number, :windows_file_id, :identity_source, :original_path, :previous_path,
            :current_path, :file_name, :extension, :size, :created_time, :modified_time, :sha256, :previous_sha256,
            :scan_root, :is_active, :content_identity_status, :last_error, 0
        )
        """,
        event,
    )


def event_type_from_flags(path_changed: bool, content_changed: bool, active_state_changed: bool, is_active: int, is_new: bool) -> str:
    if is_new:
        return "new_file"
    if active_state_changed and not is_active:
        return "missing_file"
    if active_state_changed and is_active:
        return "restored_file"
    if path_changed and content_changed:
        return "path_and_content_changed"
    if path_changed:
        return "path_changed"
    if content_changed:
        return "content_changed"
    return "metadata_refreshed"


def file_to_master_row(observed: FileObservation) -> Dict[str, object]:
    return {
        "file_key": observed.file_key,
        "volume_serial_number": observed.volume_serial_number,
        "windows_file_id": observed.windows_file_id,
        "identity_source": observed.identity_source,
        "original_path": observed.original_path,
        "current_path": observed.current_path,
        "file_name": observed.file_name,
        "extension": observed.extension,
        "size": observed.size,
        "created_time": observed.created_time,
        "modified_time": observed.modified_time,
        "first_seen_at": observed.first_seen_at,
        "last_seen_at": observed.last_seen_at,
        "scan_root": observed.scan_root,
        "is_active": observed.is_active,
        "metadata_signature": observed.metadata_signature,
    }


def file_to_current_row(observed: FileObservation, original_path: Optional[str] = None, first_seen_at: Optional[str] = None) -> Dict[str, object]:
    row = file_to_master_row(observed)
    row["original_path"] = original_path or observed.original_path
    row["first_seen_at"] = first_seen_at or observed.first_seen_at
    row["current_sha256"] = observed.current_sha256
    row["content_identity_status"] = observed.content_identity_status
    return row


def exported_event_ids(tracker_path: str) -> set[int]:
    ids: set[int] = set()
    if not os.path.exists(tracker_path):
        return ids
    with open(tracker_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            event_id = payload.get("id")
            if isinstance(event_id, int):
                ids.add(event_id)
    return ids


def export_pending_events(current_conn: sqlite3.Connection, tracker_path: str) -> int:
    ensure_parent(tracker_path)
    rows = current_conn.execute("SELECT * FROM change_events WHERE exported_to_jsonl = 0 ORDER BY id").fetchall()
    if not rows:
        return 0
    known_ids = exported_event_ids(tracker_path)
    with open(tracker_path, "a", encoding="utf-8") as handle:
        for row in rows:
            if row["id"] in known_ids:
                continue
            payload = dict(row)
            payload.pop("exported_to_jsonl", None)
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    current_conn.executemany("UPDATE change_events SET exported_to_jsonl = 1 WHERE id = ?", [(row["id"],) for row in rows])
    current_conn.commit()
    return len(rows)


def mirror_pending_master_additions(current_conn: sqlite3.Connection, master_conn: sqlite3.Connection) -> int:
    rows = current_conn.execute("SELECT * FROM pending_master_additions WHERE mirrored_to_master = 0 ORDER BY rowid").fetchall()
    if not rows:
        return 0
    for row in rows:
        payload = dict(row)
        payload.pop("mirrored_to_master", None)
        insert_master_row(master_conn, payload)
    master_conn.commit()
    current_conn.executemany("DELETE FROM pending_master_additions WHERE file_key = ?", [(row["file_key"],) for row in rows])
    current_conn.commit()
    return len(rows)


def should_hash_existing(stage: str, row: sqlite3.Row, changed_hint: bool, threshold_bytes: int) -> bool:
    if stage != "hash":
        return False
    if changed_hint:
        return True
    return row["content_identity_status"] != "hashed" and row["size"] <= threshold_bytes


def stage1_load(target_path: str, master_conn: sqlite3.Connection, current_conn: sqlite3.Connection, top_level_only: bool, hash_new_files: bool, exclude_paths: set[str]) -> Dict[str, int]:
    scan_root = os.path.abspath(target_path)
    files_seen = 0
    with current_conn:
        for path in iterate_files(scan_root, top_level_only, exclude_paths):
            observed = observe_path(path, scan_root, hash_new_files)
            if not observed:
                continue
            files_seen += 1
            insert_master_row(master_conn, file_to_master_row(observed))
            upsert_current_row(current_conn, file_to_current_row(observed))
    master_conn.commit()
    return {"files_seen": files_seen}


def stage2_update(target_path: str, master_conn: sqlite3.Connection, current_conn: sqlite3.Connection, tracker_path: str, top_level_only: bool, stage: str, opportunistic_hash_bytes: int, exclude_paths: set[str]) -> Dict[str, int]:
    scan_root = os.path.abspath(target_path)
    existing = current_rows_by_scan_root(current_conn, scan_root)
    observed_map: Dict[str, FileObservation] = {}
    counts = {
        "files_seen": 0,
        "new_files": 0,
        "path_changed": 0,
        "content_changed": 0,
        "missing_files": 0,
        "restored_files": 0,
    }

    with current_conn:
        for path in iterate_files(scan_root, top_level_only, exclude_paths):
            try:
                identity = get_windows_identity(path)
            except Exception:
                continue
            file_key = file_key_from_identity(identity["volume_serial_number"], identity["windows_file_id"])
            previous = existing.get(file_key)
            size = os.path.getsize(path)
            modified_time = iso_from_ts(os.path.getmtime(path))
            changed_hint = previous is None or previous["current_path"] != path or previous["size"] != size or previous["modified_time"] != modified_time or previous["is_active"] == 0
            hash_now = stage == "hash" if previous is None else should_hash_existing(stage, previous, changed_hint, opportunistic_hash_bytes)

            observed = observe_path(path, scan_root, hash_now)
            if not observed:
                continue
            counts["files_seen"] += 1
            observed_map[file_key] = observed

            if previous is None:
                upsert_current_row(current_conn, file_to_current_row(observed))
                queue_master_addition(current_conn, file_to_master_row(observed))
                insert_event(
                    current_conn,
                    {
                        "observed_at": utc_now(),
                        "stage": stage,
                        "event_type": "new_file",
                        "path_changed": 0,
                        "content_changed": 1 if observed.current_sha256 else 0,
                        "active_state_changed": 1,
                        "file_key": observed.file_key,
                        "volume_serial_number": observed.volume_serial_number,
                        "windows_file_id": observed.windows_file_id,
                        "identity_source": observed.identity_source,
                        "original_path": observed.original_path,
                        "previous_path": None,
                        "current_path": observed.current_path,
                        "file_name": observed.file_name,
                        "extension": observed.extension,
                        "size": observed.size,
                        "created_time": observed.created_time,
                        "modified_time": observed.modified_time,
                        "sha256": observed.current_sha256,
                        "previous_sha256": None,
                        "scan_root": observed.scan_root,
                        "is_active": 1,
                        "content_identity_status": observed.content_identity_status,
                        "last_error": observed.hash_error,
                    },
                )
                counts["new_files"] += 1
                continue

            previous_sha = previous["current_sha256"]
            path_changed = previous["current_path"] != observed.current_path
            was_inactive = previous["is_active"] == 0
            metadata_changed = previous["modified_time"] != observed.modified_time or previous["metadata_signature"] != observed.metadata_signature
            content_changed = previous["size"] != observed.size
            if observed.current_sha256 and previous_sha and observed.current_sha256 != previous_sha:
                content_changed = True
            if observed.current_sha256 and not previous_sha and stage == "hash" and changed_hint:
                content_changed = True
            active_state_changed = was_inactive

            current_row = file_to_current_row(
                observed,
                original_path=previous["original_path"],
                first_seen_at=previous["first_seen_at"],
            )
            if previous_sha and not current_row["current_sha256"]:
                current_row["current_sha256"] = previous_sha
                current_row["content_identity_status"] = previous["content_identity_status"]
            upsert_current_row(current_conn, current_row)

            if path_changed or content_changed or active_state_changed or metadata_changed:
                event_type = "metadata_refreshed" if metadata_changed and not (path_changed or content_changed or active_state_changed) else event_type_from_flags(path_changed, content_changed, active_state_changed, 1, False)
                insert_event(
                    current_conn,
                    {
                        "observed_at": utc_now(),
                        "stage": stage,
                        "event_type": event_type,
                        "path_changed": 1 if path_changed else 0,
                        "content_changed": 1 if content_changed else 0,
                        "active_state_changed": 1 if active_state_changed else 0,
                        "file_key": observed.file_key,
                        "volume_serial_number": observed.volume_serial_number,
                        "windows_file_id": observed.windows_file_id,
                        "identity_source": observed.identity_source,
                        "original_path": previous["original_path"],
                        "previous_path": previous["current_path"],
                        "current_path": observed.current_path,
                        "file_name": observed.file_name,
                        "extension": observed.extension,
                        "size": observed.size,
                        "created_time": observed.created_time,
                        "modified_time": observed.modified_time,
                        "sha256": current_row["current_sha256"],
                        "previous_sha256": previous_sha,
                        "scan_root": observed.scan_root,
                        "is_active": 1,
                        "content_identity_status": current_row["content_identity_status"],
                        "last_error": observed.hash_error,
                    },
                )
                if path_changed:
                    counts["path_changed"] += 1
                if content_changed:
                    counts["content_changed"] += 1
                if active_state_changed:
                    counts["restored_files"] += 1

        for file_key, previous in existing.items():
            if previous["is_active"] == 0 or file_key in observed_map:
                continue
            new_status = previous["content_identity_status"]
            if previous["current_sha256"] is None and stage == "hash":
                new_status = "deleted_before_hash"
            current_conn.execute(
                """
                UPDATE current_files
                SET is_active = 0,
                    last_seen_at = ?,
                    content_identity_status = ?
                WHERE file_key = ?
                """,
                (utc_now(), new_status, file_key),
            )
            insert_event(
                current_conn,
                {
                    "observed_at": utc_now(),
                    "stage": stage,
                    "event_type": "missing_file",
                    "path_changed": 0,
                    "content_changed": 0,
                    "active_state_changed": 1,
                    "file_key": previous["file_key"],
                    "volume_serial_number": previous["volume_serial_number"],
                    "windows_file_id": previous["windows_file_id"],
                    "identity_source": previous["identity_source"],
                    "original_path": previous["original_path"],
                    "previous_path": previous["current_path"],
                    "current_path": previous["current_path"],
                    "file_name": previous["file_name"],
                    "extension": previous["extension"],
                    "size": previous["size"],
                    "created_time": previous["created_time"],
                    "modified_time": previous["modified_time"],
                    "sha256": previous["current_sha256"],
                    "previous_sha256": previous["current_sha256"],
                    "scan_root": previous["scan_root"],
                    "is_active": 0,
                    "content_identity_status": new_status,
                    "last_error": None,
                },
            )
            counts["missing_files"] += 1

    counts["master_additions_mirrored"] = mirror_pending_master_additions(current_conn, master_conn)
    counts["tracker_events_exported"] = export_pending_events(current_conn, tracker_path)
    return counts


def recover_state(master_db_path: str, current_db_path: str, tracker_path: str) -> Dict[str, int]:
    with closing(connect_sqlite(master_db_path)) as master_conn, closing(connect_sqlite(current_db_path)) as current_conn:
        init_master(master_conn)
        init_current(current_conn)
        mirrored = mirror_pending_master_additions(current_conn, master_conn)
        exported = export_pending_events(current_conn, tracker_path)
    return {"mirrored_master_additions": mirrored, "exported_events": exported}


def verify_state(master_db_path: str, current_db_path: str, tracker_path: str) -> Dict[str, object]:
    with closing(connect_sqlite(master_db_path)) as master_conn, closing(connect_sqlite(current_db_path)) as current_conn:
        init_master(master_conn)
        init_current(current_conn)
        master_keys = {row["file_key"] for row in master_conn.execute("SELECT file_key FROM files")}
        current_keys = {row["file_key"] for row in current_conn.execute("SELECT file_key FROM current_files")}
        tracker_lines = 0
        if os.path.exists(tracker_path):
            with open(tracker_path, "r", encoding="utf-8") as handle:
                tracker_lines = sum(1 for _ in handle)
        return {
            "master_rows": master_conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "current_rows": current_conn.execute("SELECT COUNT(*) FROM current_files").fetchone()[0],
            "inactive_rows": current_conn.execute("SELECT COUNT(*) FROM current_files WHERE is_active = 0").fetchone()[0],
            "pending_master_additions": current_conn.execute("SELECT COUNT(*) FROM pending_master_additions WHERE mirrored_to_master = 0").fetchone()[0],
            "pending_jsonl_exports": current_conn.execute("SELECT COUNT(*) FROM change_events WHERE exported_to_jsonl = 0").fetchone()[0],
            "duplicate_master_file_keys": master_conn.execute("SELECT COUNT(*) FROM (SELECT file_key FROM files GROUP BY file_key HAVING COUNT(*) > 1)").fetchone()[0],
            "deleted_before_hash_rows": current_conn.execute("SELECT COUNT(*) FROM current_files WHERE content_identity_status = 'deleted_before_hash'").fetchone()[0],
            "current_rows_missing_master_anchor": len(current_keys - master_keys),
            "tracker_lines": tracker_lines,
        }


def run_index(target_path: str, master_db_path: str, current_db_path: str, tracker_path: str, top_level_only: bool = False, stage: str = "metadata", opportunistic_hash_bytes: int = 8 * 1024 * 1024) -> Dict[str, object]:
    ensure_parent(master_db_path)
    ensure_parent(current_db_path)
    ensure_parent(tracker_path)
    if not os.path.exists(tracker_path):
        open(tracker_path, "a", encoding="utf-8").close()
    absolute_master = os.path.abspath(master_db_path)
    absolute_current = os.path.abspath(current_db_path)
    absolute_tracker = os.path.abspath(tracker_path)
    exclude_paths = {
        absolute_master,
        absolute_current,
        absolute_tracker,
        absolute_master + "-wal",
        absolute_master + "-shm",
        absolute_current + "-wal",
        absolute_current + "-shm",
    }
    with closing(connect_sqlite(master_db_path)) as master_conn, closing(connect_sqlite(current_db_path)) as current_conn:
        init_master(master_conn)
        init_current(current_conn)
        if master_conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0:
            counts = stage1_load(target_path, master_conn, current_conn, top_level_only, hash_new_files=(stage == "hash"), exclude_paths=exclude_paths)
            return {"mode": "stage1", **counts, "verify": verify_state(master_db_path, current_db_path, tracker_path)}
        counts = stage2_update(target_path, master_conn, current_conn, tracker_path, top_level_only, stage, opportunistic_hash_bytes, exclude_paths)
        return {"mode": "stage2", **counts, "verify": verify_state(master_db_path, current_db_path, tracker_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backup identity index builder.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Build or update backup identity indexes.")
    run_parser.add_argument("target_path")
    run_parser.add_argument("--master-db", default="master_backup_index.sqlite")
    run_parser.add_argument("--current-db", default="current_state.sqlite")
    run_parser.add_argument("--tracker", default="backup_change_log.jsonl")
    run_parser.add_argument("--top-level-only", action="store_true")
    run_parser.add_argument("--stage", choices=["metadata", "hash"], default="metadata")
    run_parser.add_argument("--opportunistic-hash-bytes", type=int, default=8 * 1024 * 1024)

    verify_parser = subparsers.add_parser("verify", help="Verify backup identity invariants.")
    verify_parser.add_argument("--master-db", default="master_backup_index.sqlite")
    verify_parser.add_argument("--current-db", default="current_state.sqlite")
    verify_parser.add_argument("--tracker", default="backup_change_log.jsonl")

    recover_parser = subparsers.add_parser("recover", help="Replay pending master additions and JSONL exports.")
    recover_parser.add_argument("--master-db", default="master_backup_index.sqlite")
    recover_parser.add_argument("--current-db", default="current_state.sqlite")
    recover_parser.add_argument("--tracker", default="backup_change_log.jsonl")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        result = run_index(
            target_path=args.target_path,
            master_db_path=args.master_db,
            current_db_path=args.current_db,
            tracker_path=args.tracker,
            top_level_only=args.top_level_only,
            stage=args.stage,
            opportunistic_hash_bytes=args.opportunistic_hash_bytes,
        )
    elif args.command == "verify":
        result = verify_state(args.master_db, args.current_db, args.tracker)
    else:
        result = recover_state(args.master_db, args.current_db, args.tracker)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
