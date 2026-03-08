# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.1.0] — 2026-03-08

### Added

- ExifTool as optional Tier 3 fallback for video metadata extraction (duration, dimensions, creation date, make, model)
- Shared ExifTool utility module (`exiftool_common.py`) with cached availability detection, subprocess invocation, and date parsing
- Tests for ExifTool video fallback and shared utilities

### Changed

- Video metadata fallback chain expanded: binary MP4 parsing → ffprobe → exiftool → empty fallback
- ExifTool availability check is now cached per process (was re-checked on every call)
- EXIF extractor delegates subprocess logic to shared `exiftool_common` module
- Filename conflict suffix changed from `_N` to `-N` (e.g. `photo-1.jpg` instead of `photo_1.jpg`)

### Fixed

- **Duplicate detection not working**: `DedupEngine` now uses an in-memory SHA-256 hash map instead of relying solely on database queries; fixes both dry-run mode (where DB writes are skipped) and multi-threaded race conditions (where hashes weren't persisted to DB until later pipeline stages)
- Thread-safe duplicate group updates (uses `_transaction()` instead of raw `_conn` access)
- SQLite threading error when accessing database from worker threads
- `QTableWidget.currentRowChanged` signal compatibility with PySide6
- Case-insensitive enum deserialization in model `from_dict` methods

---

## [1.0.0] — 2026-03-06

### Added

**Core pipeline**
- 13-stage, deterministic, per-file processing pipeline with resume support
- Pipeline stages: INIT, CHECK_PROCESSED, PATTERN_SKIP, DETECT, HASH, DEDUP, METADATA, DATE_RESOLVE, BUILD_PATH, COPY, PAIR, CLEANUP, VERIFY
- Dry-run mode: preview organization plan and space savings without modifying files
- Multi-threaded worker pool (1–16 threads, configurable)

**File support**
- Images: JPEG, PNG, GIF, BMP, TIFF, WebP, HEIC/HEIF, AVIF, JXL, PSD, SVG, and 20+ RAW formats (CR2, CR3, NEF, ARW, DNG, RAF, RW2, …)
- Videos: MP4, MOV, AVI, MKV, WebM, MTS, M2TS, and 20+ more
- Audio: MP3, FLAC, AAC, WAV, OGG, OPUS, AIFF, ALAC, and 15+ more
- Documents: PDF, Word, Excel, PowerPoint, ODF, Markdown, HTML, JSON, YAML, EPUB, and more
- Sidecar files: XMP, AAE, THM, SRT, LRC, SUB

**Categorization**
- Screenshots (dimension-based + filename pattern)
- Originals, RAW, Edited (EXIF software tag detection)
- Social Media (WhatsApp, Facebook filename patterns)
- Motion Photos (Google, Samsung)
- Burst sequences
- Voice Notes
- Movies, Songs
- Document sub-types (PDF, Text, Word, Spreadsheet, Presentation, Code, Other)
- Collection legacy fallback with media-type sub-folders

**Metadata extraction**
- EXIF: make, model, software, orientation, DateTimeOriginal (via piexif + Pillow)
- Video: duration, codec, resolution, creation time (via FFprobe)
- Audio: title, artist, album, track, duration (via Mutagen ID3/MP4/FLAC/OGG)
- Optional MusicBrainz lookup for missing audio tags

**Date resolution**
- Priority order: EXIF DateTimeOriginal → filename regex → filesystem mtime
- Configurable regex patterns for filename date extraction
- Source provenance stored per file (metadata / parsed / inferred / none)

**Deduplication**
- Tier 1: SHA-256 exact matching (automatic, per session)
- Tier 2: Perceptual hashing (imagehash.phash) for visually similar images
- Duplicate groups with winner designation and bytes-saved tracking

**RAW + JPEG pairing**
- Automatic pairing of RAW and JPEG sidecars sharing the same stem
- Configurable policy: keep_both / keep_raw / keep_jpeg

**Image processing**
- Copy originals (lossless)
- Generate downscaled JPEG/PNG exports for Originals and RAW (configurable max resolution and quality)
- EXIF orientation preservation
- HEIC/HEIF decoding via pillow-heif; RAW decoding via rawpy

**Data persistence**
- SQLite database in WAL mode with foreign key constraints
- Tables: sessions, file_records, duplicate_groups, source_manifest
- Config snapshot per session for reproducible runs
- Session stats: files_processed, files_skipped, dupes_found, space_saved, duration_seconds

**Session management**
- Full session lifecycle state machine (PENDING → RUNNING → COMPLETED / UNDONE)
- Session archive and history browsing
- Undo completed sessions (deletes destination files, marks session UNDONE)
- Incremental scan (only new/modified files vs. previous manifest)

**GUI (PySide6 / Qt6)**
- Organize view: source selector, destination picker, dry-run, organize, live progress
- Dry-run dialog: category breakdown, duplicate summary, space estimate, per-file tree
- Session history: stats table, per-file result detail panel, undo, resume
- Settings view: full config editor with dirty-state tracking
- Collection review: browse and reclassify files in an existing destination
- System desktop notification on job completion
- Close-during-processing guard

**Space management**
- Pre-flight space check with 1.3× overhead factor and 5 GB warning threshold
- Destination lock file to prevent concurrent writes from multiple instances

**Packaging**
- PyInstaller spec for single-executable builds on Linux, macOS, Windows
- macOS `.app` bundle with Retina and dark-mode support
- Build scripts via `scripts/build.py` and `Makefile`
