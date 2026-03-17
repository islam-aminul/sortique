"""Sortique CLI — headless processing and settings management."""

from __future__ import annotations

import argparse
import json
import os
import sys

from sortique.constants import PairPolicy, SessionState
from sortique.data.config_manager import ConfigManager
from sortique.data.models import FileRecord
from sortique.factory import AppFactory


# -----------------------------------------------------------------------
# Top-level parser with subcommands
# -----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for CLI mode."""
    fmt = argparse.RawDescriptionHelpFormatter

    parser = argparse.ArgumentParser(
        prog="sortique",
        formatter_class=fmt,
        description=(
            "Sortique — intelligent file organizer\n"
            "\n"
            "Automatically categorize, deduplicate, and organize photos, videos,\n"
            "audio, and documents into a clean folder structure based on metadata,\n"
            "file type, and content analysis.\n"
            "\n"
            "Run without arguments to launch the GUI."
        ),
        epilog=(
            "commands:\n"
            "  organize    Scan source directories and organize files into destination\n"
            "  config      View and manage all application settings\n"
            "\n"
            "quick start:\n"
            "  sortique organize -s ~/Downloads -d ~/Organized\n"
            "  sortique organize -s ~/Photos ~/DCIM -d /mnt/nas/Media --threads 8\n"
            "  sortique config list\n"
            "  sortique config set threads 8\n"
            "\n"
            "log output:\n"
            "  Each session generates a log at <destination>/logs/YYYYMMDD-HHMM.log\n"
            "  containing a timestamped record of every file processed.\n"
            "\n"
            "config location:\n"
            "  User overrides are stored in ~/.sortique/config.json\n"
            "  Database and session history: ~/.sortique/sortique.db"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    # --- organize ---
    org = sub.add_parser(
        "organize",
        formatter_class=fmt,
        help="Organize files from source to destination",
        description=(
            "Scan one or more source directories and organize all recognized files\n"
            "into the destination using Sortique's 13-stage pipeline:\n"
            "\n"
            "  1. Scan & discover files\n"
            "  2. Detect content type (magic bytes + extension)\n"
            "  3. SHA-256 deduplication\n"
            "  4. Metadata extraction (EXIF, video, audio tags)\n"
            "  5. Date resolution (metadata -> filename -> mtime fallback)\n"
            "  6. Categorize (RAW, Screenshots, Social Media, Songs, etc.)\n"
            "  7. Generate destination path with conflict resolution\n"
            "  8. Copy files atomically\n"
            "  9. Verify and log results\n"
            "\n"
            "Files are categorized into:\n"
            "  Images   : RAW, Originals, Screenshots, Social Media, Edited,\n"
            "             Bursts, Motion Photos, Hidden, Other\n"
            "  Videos   : Camera, Mobile, Clips, WhatsApp, Social Media,\n"
            "             Movies, Bursts, Other\n"
            "  Audio    : Songs, Voice Notes, Call Recordings, WhatsApp Audio, Other\n"
            "  Documents: PDF, Text, Word, Excel, PowerPoint, Code, etc."
        ),
        epilog=(
            "examples:\n"
            "  # Organize a single folder\n"
            "  sortique organize -s ~/Downloads -d ~/Organized\n"
            "\n"
            "  # Organize multiple sources with 8 threads\n"
            "  sortique organize -s ~/Photos ~/DCIM ~/WhatsApp -d /mnt/nas/Media -t 8\n"
            "\n"
            "  # Preview what would happen (no files copied)\n"
            "  sortique organize -s ~/Downloads -d ~/Organized --dry-run --verbose\n"
            "\n"
            "output:\n"
            "  Progress is shown on stderr.  A detailed session log is written to\n"
            "  <destination>/logs/YYYYMMDD-HHMM.log with per-file status, source\n"
            "  path, and destination path or error message.\n"
            "\n"
            "exit codes:\n"
            "  0  All files processed successfully (or dry run completed)\n"
            "  1  One or more errors occurred, or session was interrupted"
        ),
    )
    org.add_argument(
        "--source", "-s",
        nargs="+",
        required=True,
        metavar="DIR",
        help="one or more source directories to scan (recursive)",
    )
    org.add_argument(
        "--destination", "-d",
        required=True,
        metavar="DIR",
        help="root destination directory for organized output",
    )
    org.add_argument(
        "--threads", "-t",
        type=int,
        default=None,
        metavar="N",
        help="number of parallel worker threads (default: config value, usually 4)",
    )
    org.add_argument(
        "--dry-run",
        action="store_true",
        help="simulate the run — show what would happen without copying any files",
    )
    org.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="print per-file results to stdout during dry run",
    )

    # --- config ---
    cfg = sub.add_parser(
        "config",
        formatter_class=fmt,
        help="View and manage settings",
        description=(
            "View and manage Sortique settings.\n"
            "\n"
            "Settings are layered:  built-in defaults < user overrides < runtime flags.\n"
            "User overrides are persisted to ~/.sortique/config.json and survive restarts.\n"
            "Only values that differ from defaults are stored.\n"
            "\n"
            "Scalar settings (use 'set'):\n"
            "  threads                  Number of parallel worker threads (1-32)\n"
            "  jpeg_quality             JPEG export quality, 1-100 (default: 85)\n"
            "  max_resolution           Max export dimensions [width, height]\n"
            "  verify_copies            Verify copied files after write (true/false)\n"
            "  follow_symlinks          Follow symbolic links during scan (true/false)\n"
            "  musicbrainz_enabled      Enable MusicBrainz online lookup (true/false)\n"
            "  screenshot_tolerance     Pixel tolerance for screenshot detection\n"
            "\n"
            "List settings (use 'add' / 'remove'):\n"
            "  editor_patterns          Software names that mark an image as Edited\n"
            "  editor_exclusions        Software names excluded from editor detection\n"
            "  social_media_image_patterns   Filename globs for social media images\n"
            "  social_media_video_patterns   Filename globs for social media videos\n"
            "  motion_photo_patterns    Filename globs for motion photos\n"
            "  screenshot_filename_patterns  Filename globs for screenshots\n"
            "  voice_note_patterns      Filename globs for voice notes\n"
            "  burst_filename_patterns  Filename globs for burst photos\n"
            "  skip_filename_patterns   Filename globs to skip entirely\n"
            "  call_recording_patterns  Filename globs for call recordings\n"
            "  sidecar_extensions       File extensions treated as video sidecars\n"
            "  screenshot_resolutions   Screen resolutions for screenshot detection\n"
            "  date_regex_patterns      Regex patterns for extracting dates from filenames"
        ),
        epilog=(
            "examples:\n"
            "  # View all current settings and their source (default / user)\n"
            "  sortique config list\n"
            "\n"
            "  # Check a specific setting\n"
            "  sortique config get threads\n"
            "  sortique config get skip_filename_patterns\n"
            "\n"
            "  # Change scalar settings\n"
            "  sortique config set threads 8\n"
            "  sortique config set jpeg_quality 92\n"
            "  sortique config set musicbrainz_enabled true\n"
            "  sortique config set verify_copies true\n"
            "  sortique config set max_resolution '[1920, 1080]'\n"
            "\n"
            "  # Add patterns for file categorization\n"
            '  sortique config add skip_filename_patterns "*.tmp"\n'
            '  sortique config add skip_filename_patterns "Thumbs.db"\n'
            '  sortique config add call_recording_patterns "Record_*"\n'
            '  sortique config add editor_patterns "Canva"\n'
            '  sortique config add social_media_image_patterns "SNAP_*"\n'
            '  sortique config add voice_note_patterns "VN_*"\n'
            '  sortique config add sidecar_extensions ".json"\n'
            "\n"
            "  # Remove a pattern\n"
            '  sortique config remove editor_patterns "Picasa"\n'
            '  sortique config remove skip_filename_patterns "*.tmp"\n'
            "\n"
            "  # Reset a setting to its built-in default\n"
            "  sortique config reset threads\n"
            "  sortique config reset skip_filename_patterns"
        ),
    )
    cfg_sub = cfg.add_subparsers(dest="config_action")

    cfg_sub.add_parser(
        "list",
        formatter_class=fmt,
        help="Show all current settings with their source (default/user)",
        description="Display every setting with its current value and whether it\ncomes from the built-in defaults or user overrides.",
    )

    cfg_get = cfg_sub.add_parser(
        "get",
        formatter_class=fmt,
        help="Show the current value of a setting",
        description="Print the effective value of a single setting key.",
        epilog=(
            "examples:\n"
            "  sortique config get threads\n"
            "  sortique config get editor_patterns\n"
            "  sortique config get musicbrainz_enabled"
        ),
    )
    cfg_get.add_argument("key", help="setting name (e.g. threads, jpeg_quality, editor_patterns)")

    cfg_set = cfg_sub.add_parser(
        "set",
        formatter_class=fmt,
        help="Set a scalar (non-list) setting",
        description=(
            "Set a scalar setting to a new value and persist to ~/.sortique/config.json.\n"
            "\n"
            "Booleans accept: true/false, yes/no, on/off, 1/0\n"
            "Integers are auto-detected from the existing type.\n"
            "JSON values (e.g. '[1920, 1080]') are parsed for complex types like max_resolution."
        ),
        epilog=(
            "examples:\n"
            "  sortique config set threads 8\n"
            "  sortique config set jpeg_quality 92\n"
            "  sortique config set musicbrainz_enabled true\n"
            "  sortique config set verify_copies false\n"
            "  sortique config set max_resolution '[1920, 1080]'"
        ),
    )
    cfg_set.add_argument("key", help="setting name (must be a scalar, not a list)")
    cfg_set.add_argument("value", help="new value (type is inferred from the existing setting)")

    cfg_add = cfg_sub.add_parser(
        "add",
        formatter_class=fmt,
        help="Add a value to a list setting",
        description=(
            "Append a new entry to a list-type setting.\n"
            "Duplicate values are silently ignored."
        ),
        epilog=(
            "examples:\n"
            '  sortique config add skip_filename_patterns "*.tmp"\n'
            '  sortique config add call_recording_patterns "Record_*"\n'
            '  sortique config add editor_patterns "Canva"\n'
            '  sortique config add social_media_image_patterns "SNAP_*"\n'
            '  sortique config add voice_note_patterns "VN_*"\n'
            '  sortique config add sidecar_extensions ".json"'
        ),
    )
    cfg_add.add_argument("key", help="setting name (must be a list)")
    cfg_add.add_argument("value", help="value to append to the list")

    cfg_remove = cfg_sub.add_parser(
        "remove",
        formatter_class=fmt,
        help="Remove a value from a list setting",
        description="Remove a specific entry from a list-type setting.\nFails if the value is not present.",
        epilog=(
            "examples:\n"
            '  sortique config remove editor_patterns "Picasa"\n'
            '  sortique config remove skip_filename_patterns "*.tmp"\n'
            '  sortique config remove sidecar_extensions ".aae"'
        ),
    )
    cfg_remove.add_argument("key", help="setting name (must be a list)")
    cfg_remove.add_argument("value", help="exact value to remove from the list")

    cfg_reset = cfg_sub.add_parser(
        "reset",
        formatter_class=fmt,
        help="Reset a setting to its built-in default",
        description="Remove the user override for a setting, reverting it to the\nbuilt-in default from config/defaults.json.",
        epilog=(
            "examples:\n"
            "  sortique config reset threads\n"
            "  sortique config reset skip_filename_patterns\n"
            "  sortique config reset editor_patterns"
        ),
    )
    cfg_reset.add_argument("key", help="setting name to reset")

    return parser


def dispatch_cli(args: argparse.Namespace) -> int:
    """Route to the appropriate CLI handler. Returns exit code."""
    if args.command == "organize":
        return run_organize(args)
    if args.command == "config":
        return run_config(args)
    # No subcommand given — print help.
    build_parser().print_help()
    return 0


# -----------------------------------------------------------------------
# config subcommand
# -----------------------------------------------------------------------

def _parse_value(raw: str, current: object | None = None):
    """Parse a CLI value string into the appropriate Python type.

    Uses the type of *current* (the existing value) as a hint.
    Falls back to JSON parsing, then plain string.
    """
    if isinstance(current, bool):
        if raw.lower() in ("true", "1", "yes", "on"):
            return True
        if raw.lower() in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"Expected a boolean, got {raw!r}")
    if isinstance(current, int):
        return int(raw)
    # Try JSON for complex types (lists, nested values).
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _format_value(value: object) -> str:
    """Pretty-format a config value for terminal display."""
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(v, str) for v in value):
            lines = "\n    ".join(value)
            return f"\n    {lines}"
        return json.dumps(value, indent=2)
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def run_config(args: argparse.Namespace) -> int:
    """Handle the ``config`` subcommand."""
    config = ConfigManager()

    if args.config_action == "list":
        merged = config.get_all()
        defaults = config._defaults
        user = config._user
        for key in sorted(merged):
            source = "user" if key in user else "default"
            print(f"{key} ({source}): {_format_value(merged[key])}")
        return 0

    if args.config_action == "get":
        val = config.get(args.key)
        if val is None:
            print(f"Unknown setting: {args.key}", file=sys.stderr)
            return 1
        print(f"{args.key}: {_format_value(val)}")
        return 0

    if args.config_action == "set":
        current = config.get(args.key)
        if current is None:
            print(f"Unknown setting: {args.key}", file=sys.stderr)
            return 1
        if isinstance(current, list):
            print(
                f"Error: '{args.key}' is a list. Use 'config add' / 'config remove' instead.",
                file=sys.stderr,
            )
            return 1
        try:
            parsed = _parse_value(args.value, current)
            config.save_user_config({args.key: parsed})
            print(f"{args.key} = {_format_value(parsed)}")
        except (ValueError, TypeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.config_action == "add":
        current = config.get(args.key)
        if current is None:
            print(f"Unknown setting: {args.key}", file=sys.stderr)
            return 1
        if not isinstance(current, list):
            print(f"Error: '{args.key}' is not a list setting.", file=sys.stderr)
            return 1
        new_list = list(current)
        value = args.value
        if value in new_list:
            print(f"'{value}' already in {args.key}.", file=sys.stderr)
            return 0
        new_list.append(value)
        try:
            config.save_user_config({args.key: new_list})
            print(f"Added '{value}' to {args.key}")
        except (ValueError, TypeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.config_action == "remove":
        current = config.get(args.key)
        if current is None:
            print(f"Unknown setting: {args.key}", file=sys.stderr)
            return 1
        if not isinstance(current, list):
            print(f"Error: '{args.key}' is not a list setting.", file=sys.stderr)
            return 1
        new_list = list(current)
        value = args.value
        if value not in new_list:
            print(f"'{value}' not found in {args.key}.", file=sys.stderr)
            return 1
        new_list.remove(value)
        try:
            config.save_user_config({args.key: new_list})
            print(f"Removed '{value}' from {args.key}")
        except (ValueError, TypeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.config_action == "reset":
        defaults = config._defaults
        if args.key not in defaults:
            print(f"Unknown setting: {args.key}", file=sys.stderr)
            return 1
        # Save the default value, which strips it from user config.
        config.save_user_config({args.key: defaults[args.key]})
        print(f"{args.key} reset to default: {_format_value(defaults[args.key])}")
        return 0

    # No config action — print config help.
    build_parser().parse_args(["config", "--help"])
    return 0


# -----------------------------------------------------------------------
# organize subcommand
# -----------------------------------------------------------------------

def run_organize(args: argparse.Namespace) -> int:
    """Run a full organize session in headless mode.

    Returns 0 on success, 1 on error.
    """
    source_dirs = [os.path.abspath(s) for s in args.source]
    destination = os.path.abspath(args.destination)

    # --- validate ---
    for src in source_dirs:
        if not os.path.isdir(src):
            print(f"Error: source directory does not exist: {src}", file=sys.stderr)
            return 1

    os.makedirs(destination, exist_ok=True)

    # --- bootstrap ---
    factory = AppFactory()

    if args.threads is not None:
        factory.config.set("threads", args.threads)

    sm = factory.session_manager()
    session = sm.create_session(source_dirs, destination)
    session_id = session.id

    try:
        sm.transition(session_id, SessionState.IN_PROGRESS)

        # --- scan ---
        print(f"Scanning {len(source_dirs)} source(s)…", file=sys.stderr)
        scanner = factory.scanner()
        scan_result = scanner.scan(source_dirs)
        print(
            f"Found {len(scan_result.files):,} files "
            f"({scan_result.total_bytes / (1024 * 1024):.1f} MB)",
            file=sys.stderr,
        )

        if not scan_result.files:
            print("Nothing to organize.", file=sys.stderr)
            return 0

        # --- build file records ---
        records: list[FileRecord] = []
        for sf in scan_result.files:
            rec = FileRecord(
                session_id=session_id,
                source_path=sf.path,
                source_dir=sf.source_dir,
                file_size=sf.size,
                pair_policy=PairPolicy.KEEP_BOTH,
            )
            records.append(rec)
        factory.db.create_file_records_batch(records)

        # --- dry run ---
        if args.dry_run:
            pipe = factory.pipeline(destination, dry_run=True)
            pipe._session_id = session_id
            for rec in records:
                result = pipe.process_file(rec)
                if args.verbose:
                    print(
                        f"{result.final_status.value.upper():9s} | "
                        f"{rec.source_path} | "
                        f"{rec.destination_path or result.skip_reason or result.error_message or ''}",
                    )
            print(f"\nDry run complete — {len(records):,} files previewed.", file=sys.stderr)
            return 0

        # --- process ---
        sm.transition(session_id, SessionState.RUNNING)

        pool = factory.thread_pool(destination, source_dirs)
        pool._pipeline._session_id = session_id

        def _on_progress(prog):
            total = prog.total_files
            done = prog.processed
            pct = int(done / total * 100) if total else 0
            print(
                f"\r  [{done:,}/{total:,}] {pct}% | "
                f"{prog.files_per_second:.1f} files/s | "
                f"Errors: {prog.errors}",
                end="",
                flush=True,
                file=sys.stderr,
            )

        pool.start(records, progress_callback=_on_progress)

        try:
            final = pool.wait()
        except KeyboardInterrupt:
            print("\nStopping…", file=sys.stderr)
            pool.stop()
            final = pool.wait()
            sm.transition(session_id, SessionState.STOPPED)
            print("Session stopped. Can be resumed later.", file=sys.stderr)
            return 1

        print(file=sys.stderr)  # newline after progress line

        # --- finalize ---
        sm.finalize_session(session_id)

        completed = final.processed - final.skipped - final.errors
        print(
            f"\nDone! Completed: {completed:,} | "
            f"Skipped: {final.skipped:,} | "
            f"Errors: {final.errors} | "
            f"Duration: {final.elapsed_seconds:.1f}s",
        )

        if pool._session_logger is not None:
            print(f"Log: {pool._session_logger.log_path}")

        return 0 if final.errors == 0 else 1

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        try:
            sm.transition(session_id, SessionState.STOPPED)
        except Exception:
            pass
        return 1
    except Exception as exc:
        print(f"\nFatal error: {exc}", file=sys.stderr)
        try:
            sm.transition(session_id, SessionState.ERROR)
        except Exception:
            pass
        return 1
    finally:
        factory.close()
