# Sortique

Sortique is a smart desktop file organizer that automatically categorizes, deduplicates, and organizes photos, videos, audio, and documents. It extracts metadata from EXIF, audio tags, and filenames to build a clean, date-organized folder structure — with a dry-run preview before touching any files.

---

## Features

- **13-stage processing pipeline** — deterministic, resumable per-file workflow
- **Content detection** — magic-byte MIME detection for images, video, audio, documents, and RAW formats
- **Smart categorization** — Screenshots, Originals, RAW, Edited, Social Media, Motion Photos, Voice Notes, Bursts, Movies, Songs, Documents, and more
- **Two-tier deduplication** — exact SHA-256 matching + perceptual image hashing (phash)
- **RAW + JPEG pairing** — links sidecar pairs with configurable keep policy
- **Metadata extraction** — EXIF (Pillow/piexif), video streams (FFmpeg), audio tags (Mutagen), MusicBrainz lookup (optional)
- **Date resolution** — EXIF → filename regex → filesystem mtime fallback
- **Dry-run preview** — inspect organization plan and space savings before committing
- **Undo** — revert completed sessions back to source
- **Session history** — browse past runs, stats, per-file results, archived sessions
- **Collection review** — inspect and reclassify files in an existing organized destination
- **Multi-threaded** — configurable worker pool (1–16 threads)
- **Cross-platform** — Linux, macOS, Windows (PySide6 / Qt6)

---

## Screenshots

> _Screenshots coming soon._

---

## Installation

**From PyPI:**

```bash
pip install sortique
```

**Requirements:**

- Python ≥ 3.11
- FFmpeg on `PATH` (video metadata extraction)
- libmagic (magic-byte detection — `brew install libmagic` / `apt install libmagic1`)

**Python dependencies** (installed automatically):

| Package | Purpose |
|---|---|
| PySide6 ≥ 6.6 | Qt6 GUI |
| Pillow ≥ 10 | Image processing |
| piexif | EXIF read/write |
| rawpy | RAW image decoding |
| pillow-heif | HEIC/HEIF support |
| mutagen | Audio tag extraction |
| imagehash | Perceptual hashing |
| python-magic | Magic-byte detection |
| musicbrainzngs | MusicBrainz lookup (optional) |

---

## Usage

Launch the GUI:

```bash
sortique
# or
python -m sortique
```

**Basic workflow:**

1. **Add sources** — click _Add Source_ in the Organize view and select one or more directories.
2. **Set destination** — choose where organized files should be written.
3. **Preview** — click _Dry Run_ to see the proposed structure, duplicate count, and space savings.
4. **Organize** — click _Organize_ to run the full pipeline. Progress is shown in real time.
5. **Review** — open _Session History_ to inspect per-file results or undo the session.

---

## Configuration

Settings are managed via the in-app **Settings** view or by editing the config file directly.

**Config file location:**

| Platform | Path |
|---|---|
| Linux / macOS | `~/.sortique/config.json` |
| Windows | `%USERPROFILE%\.sortique\config.json` |

See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the full reference.

**Key options at a glance:**

| Option | Default | Description |
|---|---|---|
| `threads` | `4` | Worker threads (1–16) |
| `jpeg_quality` | `85` | Export JPEG quality (1–100) |
| `verify_copies` | `false` | Checksum-verify each copied file |
| `follow_symlinks` | `false` | Follow symbolic links when scanning |
| `musicbrainz_enabled` | `false` | Enable MusicBrainz audio lookups |

---

## Building from Source

```bash
git clone https://github.com/yourorg/sortique.git
cd sortique
pip install -r requirements.txt
python -m sortique
```

Run the test suite:

```bash
pytest tests/
```

---

## Packaging

Build a standalone executable with PyInstaller:

```bash
pip install pyinstaller
pyinstaller sortique.spec
```

Output is written to `dist/sortique` (Linux/macOS) or `dist/sortique.exe` (Windows). On macOS, a `Sortique.app` bundle is produced automatically.

To build for all platforms, run the packaging script:

```bash
python scripts/build.py
```

---

## License

MIT — see [LICENSE](LICENSE).
