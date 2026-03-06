# Architecture

## High-Level Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                          UI Layer (ui/)                         │
│  MainWindow  OrganizeView  DryRunDialog  SessionHistoryView     │
│  SettingsView  CollectionReviewView                             │
│        │              │ (Qt signals / worker threads)           │
└────────┼──────────────┼──────────────────────────────────────── ┘
         │              │
         ▼              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Service Layer (service/)                   │
│                                                                 │
│  Pipeline ──► FileProcessorPool ──► [worker threads]           │
│  SessionManager   DryRunManager   UndoManager                  │
│  SpaceChecker     CollectionReviewer   NotificationService      │
│        │                                                        │
└────────┼──────────────────────────────────────────────────────  ┘
         │
    ┌────┴────────────────────────────────────────────────┐
    ▼                                                     ▼
┌───────────────────────────┐     ┌──────────────────────────────┐
│     Engine Layer (engine/)│     │     Data Layer (data/)       │
│                           │     │                              │
│  Scanner                  │     │  Database (SQLite WAL)       │
│  ContentDetector          │     │    sessions                  │
│  FileHasher               │     │    file_records              │
│  DedupEngine              │     │    duplicate_groups          │
│  ExifExtractor            │     │    source_manifest           │
│  DateParser               │     │                              │
│  Categorizer              │     │  ConfigManager               │
│  PathGenerator            │     │  FileSystemHelper            │
│  BurstDetector            │     │  LockManager                 │
│  PairDetector             │     │                              │
│  ImageProcessor           │     └──────────────────────────────┘
│  VideoProcessor           │
│  AudioProcessor           │
│  DocumentProcessor        │
│  AudioMetadataExtractor   │
│  VideoMetadataExtractor   │
│  MusicBrainzClient        │
└───────────────────────────┘

        AppFactory (factory.py) — wires all layers via DI
```

---

## Module Descriptions

### `factory.py` — AppFactory

Dependency injection container. Every engine, service, and data object is created and cached here. Stateless singletons (e.g., `ContentDetector`, `FileHasher`) are cached on first access; stateful objects (e.g., `Pipeline`, `PathGenerator`) are constructed fresh on each call. `AppFactory.close()` releases all held resources.

### `app.py` — Entry Point

Creates the `QApplication`, instantiates `AppFactory`, passes it to `MainWindow`, and starts the Qt event loop. On exit, calls `AppFactory.close()` to flush the database and release locks.

### `constants.py` — Enumerations and Constants

Defines all shared enumerations (`FileType`, `SessionState`, `FileStatus`, `DateSource`, `ExifStatus`, `DupMatchType`, `PairPolicy`), the `EXTENSION_MAP` dict mapping file extensions to `FileType`, file-type sets, pipeline thresholds, and cloud-stub detection patterns. No logic — import-safe.

---

### `data/` — Data Layer

**`config_manager.py`** — Loads `config/defaults.json`, merges the user's `~/.sortique/config.json` on top, and exposes typed attribute access. Validates `threads` (1–16) and `jpeg_quality` (1–100) on write. `save()` persists user overrides without touching defaults.

**`database.py`** — Thin SQLite wrapper. Opens the database in WAL mode with foreign key constraints. Owns `CREATE TABLE` and index DDL, executed on first open. Exposes `execute()`, `executemany()`, and `fetchall()` helpers. `close()` flushes the WAL and releases the connection.

**`models.py`** — Pure dataclasses (no ORM): `Session`, `FileRecord`, `DuplicateGroup`, `SourceManifestEntry`. Serialise to/from plain dicts for database storage.

**`file_system.py`** — `FileSystemHelper` static utility class: hidden/system file detection, cloud-stub detection (iCloud `.icloud`, Dropbox `.dropbox`), symlink resolution, filename sanitization (cross-platform, with optional `target_os="windows"` mode), directory skips, and free-space queries.

**`lock_manager.py`** — Acquires a filesystem lock file inside the destination directory to prevent two concurrent Sortique instances from writing to the same destination. Context-manager interface; released on `close()`.

---

### `engine/` — Engine Layer

**`scanner.py`** — Recursively walks source directories with `os.scandir`. Skips hidden files, system files, symlinks (configurable), cloud stubs, and temp files. Tracks inodes to detect symlink cycles. Supports incremental scanning (new/modified files only) and builds `SourceManifest` entries for session resumption.

**`detector.py`** — `ContentDetector` uses `python-magic` (libmagic) to read the first 256 bytes of a file and return a MIME type and `FileType`. Falls back to extension lookup when magic is inconclusive.

**`hasher.py`** — `FileHasher` computes SHA-256 in 64 KB chunks. Also exposes `perceptual_hash()` using `imagehash.phash` for image similarity (used by `DedupEngine` in perceptual mode).

**`dedup.py`** — `DedupEngine` maintains an in-memory SHA-256 → `FileRecord` map per session. On each new file: exact-match check first; if enabled, perceptual-hash comparison as a second pass. Groups duplicates into `DuplicateGroup` records; designates the first-seen file as the winner.

**`categorizer.py`** — `Categorizer` applies ordered rules to classify each file into a category string (e.g., `"Screenshots"`, `"Originals"`, `"RAW"`, `"Edited"`, `"Social Media"`, `"Motion Photos"`, `"Bursts"`, `"Voice Notes"`, `"Movies"`, `"Songs"`, `"Documents/PDF"`, etc.). Rules check filename patterns, EXIF fields, and pixel dimensions (for screenshots).

**`path_generator.py`** — `PathGenerator` maps a `(category, FileType, date, make/model)` tuple to a destination path using a clean folder structure. Uses `target_os="windows"` sanitization so paths are safe across all platforms. Resolves filename conflicts by appending `_1`, `_2`, … (up to `MAX_CONFLICT_ATTEMPTS`).

**`burst_detector.py`** — Groups files with burst-pattern filenames or sequential timestamps into burst sequences. Tags each file with a burst index.

**`pair_detector.py`** — Pairs RAW and JPEG files sharing the same stem. Applied as a post-pipeline batch pass. Honours `PairPolicy` (keep_both / keep_raw / keep_jpeg).

**`metadata/date_parser.py`** — `DateParser` tries, in order: EXIF `DateTimeOriginal`, configurable filename regex patterns, file modification time. Returns a `DateResult` with the resolved `datetime` and source provenance.

**`metadata/exif_extractor.py`** — `ExifExtractor` wraps `piexif` and `Pillow` to read make, model, software, orientation, width, height, and `DateTimeOriginal`. Returns an `ExifResult` with an `ExifStatus` enum (ok / partial / error / none).

**`metadata/video_metadata.py`** — `VideoMetadataExtractor` calls `ffprobe` (subprocess) to read duration, codec, resolution, and creation time from video container metadata.

**`metadata/audio_metadata.py`** — `AudioMetadataExtractor` uses Mutagen to read ID3 (MP3), MP4, FLAC, OGG, and other tag formats. Extracts title, artist, album, track number, and duration.

**`metadata/musicbrainz_client.py`** — Optional `MusicBrainzClient` queries the MusicBrainz API to fill in missing audio metadata. Rate-limited; disabled by default.

**`processors/`** — Four processors (`ImageProcessor`, `VideoProcessor`, `AudioProcessor`, `DocumentProcessor`) execute the copy-and-transform step for each file type. `ImageProcessor` also generates downscaled JPEG/PNG exports when the source is Originals or RAW.

---

### `service/` — Service Layer

**`pipeline.py`** — `Pipeline` executes the 13-stage workflow for each file (INIT → CHECK_PROCESSED → PATTERN_SKIP → DETECT → HASH → DEDUP → METADATA → DATE_RESOLVE → BUILD_PATH → COPY → PAIR → CLEANUP → VERIFY). Each stage is isolated — per-file errors are caught and recorded without halting the batch. Supports dry-run mode (skips COPY and VERIFY).

**`thread_pool.py`** — `FileProcessorPool` submits files to a `concurrent.futures.ThreadPoolExecutor` and collects results. Thread count comes from `config.threads`.

**`session_manager.py`** — `SessionManager` creates, loads, updates, and archives sessions. Validates state-machine transitions (e.g., only RUNNING → PAUSED is legal). Serialises stats and config snapshots to the database.

**`dry_run.py`** — `DryRunManager` runs the pipeline in dry-run mode and aggregates a preview report: category distribution, duplicate count, estimated space savings, and per-file destination paths.

**`undo_manager.py`** — `UndoManager` reverts a completed session: re-reads `FileRecord.destination_path` entries and deletes copied files, then marks the session as UNDONE.

**`space_checker.py`** — `SpaceChecker` calls `shutil.disk_usage` on the destination, applies a 1.3× fragmentation overhead factor and a 1.1× buffer, and raises if the estimated required space exceeds available space or falls below the 5 GB warning threshold.

**`collection_review.py`** — `CollectionReviewer` walks an existing organized destination directory and loads its file tree for inspection and reclassification via the UI.

**`notification_service.py`** — `NotificationService` emits a system desktop notification (via Qt) when an organize or dry-run job completes.

---

### `ui/` — UI Layer

**`main_window.py`** — `MainWindow` hosts the sidebar navigation and stacks the four main views. Guards against closing while a pipeline run is in progress. Shows an About dialog with version info.

**`organize_view.py`** — Source-directory list, destination picker, dry-run trigger, and organize trigger. Hosts `ScanWorker`, `DryRunWorker`, and `PipelineWorker` Qt threads; wires their signals to progress bars and status labels.

**`dry_run_view.py`** — `DryRunDialog` displays the preview report from `DryRunManager`: category breakdown table, duplicate summary, space savings estimate, and per-file destination tree.

**`session_history_view.py`** — Table of past sessions with stats. Selecting a session loads per-file results into a detail panel. Provides Undo and Resume buttons.

**`settings_view.py`** — Form-based editor for all `config.json` options. Tracks dirty state; prompts before discarding unsaved changes.

**`collection_review_view.py`** — Tree browser of an existing organized destination. Allows reclassifying individual files and moving them to a different category folder.

**`workers.py`** — `ScanWorker`, `DryRunWorker`, `PipelineWorker` — `QThread` subclasses that run blocking operations off the main thread and emit `progress`, `finished`, and `error` signals.

---

## Data Flow

```
User picks sources + destination
        │
        ▼
  Scanner.scan()  ──►  ScanResult (list[ScannedFile])
        │
        ▼
  SpaceChecker.check()  (abort if insufficient space)
        │
        ▼
  SessionManager.create()  ──►  Session persisted to DB
        │
        ▼
  FileProcessorPool  ──►  N threads
        │
        ▼  (per file, in parallel)
  Pipeline.process_file()
    1. INIT           read file stat
    2. CHECK_PROCESSED skip if already done
    3. PATTERN_SKIP   hidden / system / temp
    4. DETECT         MIME + FileType via libmagic
    5. HASH           SHA-256
    6. DEDUP          exact match → skip
    7. METADATA       EXIF / FFprobe / Mutagen
    8. DATE_RESOLVE   EXIF → filename regex → mtime
    9. BUILD_PATH     Categorizer + PathGenerator
   10. COPY           FileProcessor copies + exports
   11. PAIR           (batch pass after all files)
   12. CLEANUP        persist FileRecord to DB
   13. VERIFY         confirm destination exists
        │
        ▼
  SessionManager.complete()  ──►  stats written to DB
        │
        ▼
  NotificationService.notify()
```

---

## Database Schema

### `sessions`

```sql
CREATE TABLE sessions (
    id             TEXT PRIMARY KEY,
    state          TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    source_dirs    TEXT NOT NULL,   -- JSON array
    destination_dir TEXT NOT NULL,
    config_snapshot TEXT NOT NULL,  -- JSON snapshot
    stats          TEXT NOT NULL,   -- JSON {files_processed, files_skipped,
                                    --        dupes_found, space_saved,
                                    --        duration_seconds}
    is_archived    INTEGER NOT NULL DEFAULT 0
);
```

### `file_records`

```sql
CREATE TABLE file_records (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    source_path       TEXT NOT NULL,
    source_dir        TEXT NOT NULL,
    destination_path  TEXT,
    file_type         TEXT NOT NULL,       -- image|video|audio|document|sidecar|unknown
    content_type      TEXT NOT NULL DEFAULT '',
    category          TEXT NOT NULL DEFAULT '',
    file_size         INTEGER NOT NULL DEFAULT 0,
    sha256_hash       TEXT,
    perceptual_hash   TEXT,
    pipeline_stage    INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL,       -- pending|processing|completed|skipped|error
    skip_reason       TEXT,
    error_message     TEXT,
    date_value        TEXT,               -- ISO 8601
    date_source       TEXT NOT NULL DEFAULT 'none', -- metadata|parsed|inferred|none
    timezone_offset   TEXT,
    exif_status       TEXT NOT NULL DEFAULT 'none', -- ok|partial|error|none
    exif_data         TEXT,               -- JSON {make,model,software,...}
    is_duplicate      INTEGER NOT NULL DEFAULT 0,
    duplicate_group_id TEXT,
    pair_id           TEXT,
    pair_policy       TEXT,
    verified          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE INDEX idx_fr_session_status ON file_records(session_id, status);
CREATE INDEX idx_fr_sha256         ON file_records(sha256_hash);
CREATE INDEX idx_fr_session_stage  ON file_records(session_id, pipeline_stage);
CREATE INDEX idx_fr_dup_group      ON file_records(duplicate_group_id);
CREATE INDEX idx_fr_pair           ON file_records(pair_id);
```

### `duplicate_groups`

```sql
CREATE TABLE duplicate_groups (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(id),
    winner_file_id TEXT NOT NULL,
    hash_value     TEXT NOT NULL,
    match_type     TEXT NOT NULL,  -- exact|perceptual
    file_count     INTEGER NOT NULL DEFAULT 0,
    bytes_saved    INTEGER NOT NULL DEFAULT 0
);
```

### `source_manifest`

```sql
CREATE TABLE source_manifest (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    source_dir  TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    file_size   INTEGER NOT NULL DEFAULT 0,
    mtime       REAL NOT NULL DEFAULT 0
);

CREATE INDEX idx_sm_session_path ON source_manifest(session_id, file_path);
```
