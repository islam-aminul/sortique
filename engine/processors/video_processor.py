"""Video processing: copy-only with sidecar grouping."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from sortique.data.file_system import FileSystemHelper
from sortique.engine.processors import ProcessResult

if TYPE_CHECKING:
    from sortique.data.config_manager import ConfigManager


class VideoProcessor:
    """Video processing: copy-only with sidecar grouping.

    Videos are never re-encoded.  The processor copies the original file
    and optionally discovers and copies companion sidecar files (subtitles,
    thumbnails, XMP, etc.).
    """

    def __init__(self, config: ConfigManager) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public: process (copy single video)
    # ------------------------------------------------------------------

    def process(
        self,
        source: str,
        destination: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ProcessResult:
        """Copy *source* video to *destination* using atomic copy.

        Never re-encodes.  Returns a :class:`ProcessResult`.
        """
        try:
            file_size = os.path.getsize(source)
            FileSystemHelper.atomic_copy(source, destination, progress_callback)
            return ProcessResult(
                success=True,
                source_path=source,
                dest_path=destination,
                bytes_copied=file_size,
                is_sidecar=False,
                error=None,
            )
        except Exception as exc:
            return ProcessResult(
                success=False,
                source_path=source,
                dest_path=destination,
                bytes_copied=0,
                is_sidecar=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Public: sidecar discovery
    # ------------------------------------------------------------------

    def find_sidecars(self, video_path: str) -> list[str]:
        """Find sidecar files for *video_path* using extended stem matching.

        For ``VIDEO_0001.mp4`` in ``/media/``, this looks for:

        * **Standard stem match**: ``VIDEO_0001.srt``, ``VIDEO_0001.thm``, …
        * **Extended stem match**: ``VIDEO_0001.mp4.srt``, ``VIDEO_0001.mp4.xmp``, …

        Only files whose final extension (case-insensitive) is in
        ``config.sidecar_extensions`` are matched.
        """
        parent = os.path.dirname(video_path)
        base = os.path.basename(video_path)
        stem, _ = os.path.splitext(base)

        sidecar_exts = {e.lower() for e in self.config.sidecar_extensions}
        if not sidecar_exts:
            return []

        found: list[str] = []

        try:
            entries = os.listdir(parent)
        except OSError:
            return []

        for entry in entries:
            entry_lower = entry.lower()
            entry_path = os.path.join(parent, entry)

            # Skip the video file itself.
            if os.path.normcase(entry) == os.path.normcase(base):
                continue

            # Must be a regular file.
            if not os.path.isfile(entry_path):
                continue

            _, ext = os.path.splitext(entry_lower)

            # Standard stem match: same stem + sidecar extension.
            if (
                entry_lower.startswith(stem.lower())
                and ext in sidecar_exts
                and len(os.path.splitext(entry)[0].lower()) == len(stem)
            ):
                found.append(entry_path)
                continue

            # Extended stem match: video basename (with ext) as prefix.
            if entry_lower.startswith(base.lower()) and ext in sidecar_exts:
                found.append(entry_path)
                continue

        return sorted(found)

    # ------------------------------------------------------------------
    # Public: copy video + sidecars
    # ------------------------------------------------------------------

    def copy_with_sidecars(
        self,
        source: str,
        dest_dir: str,
        dest_filename_stem: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[ProcessResult]:
        """Copy *source* video and all its sidecars to *dest_dir*.

        The video is renamed to ``{dest_filename_stem}{original_ext}``.
        Each sidecar keeps its own extension but adopts the same stem
        (or stem + original video ext for extended matches).

        Returns a list of :class:`ProcessResult` — one for the video,
        then one per sidecar.
        """
        results: list[ProcessResult] = []

        # --- copy the video itself ---
        video_ext = os.path.splitext(source)[1]
        video_dest = os.path.join(dest_dir, f"{dest_filename_stem}{video_ext}")
        results.append(self.process(source, video_dest, progress_callback))

        # --- discover & copy sidecars ---
        sidecars = self.find_sidecars(source)
        video_base = os.path.basename(source)
        video_stem = os.path.splitext(video_base)[0]

        for sc_path in sidecars:
            sc_base = os.path.basename(sc_path)
            sc_base_lower = sc_base.lower()
            video_base_lower = video_base.lower()

            # Determine if this is an extended stem match.
            if sc_base_lower.startswith(video_base_lower):
                # Extended: e.g. VIDEO_0001.mp4.srt → stem.mp4.srt
                suffix = sc_base[len(video_base):]  # ".srt"
                sc_dest_name = f"{dest_filename_stem}{video_ext}{suffix}"
            else:
                # Standard: e.g. VIDEO_0001.srt → stem.srt
                sc_ext = os.path.splitext(sc_base)[1]
                sc_dest_name = f"{dest_filename_stem}{sc_ext}"

            sc_dest = os.path.join(dest_dir, sc_dest_name)

            try:
                file_size = os.path.getsize(sc_path)
                FileSystemHelper.atomic_copy(sc_path, sc_dest)
                results.append(ProcessResult(
                    success=True,
                    source_path=sc_path,
                    dest_path=sc_dest,
                    bytes_copied=file_size,
                    is_sidecar=True,
                    error=None,
                ))
            except Exception as exc:
                results.append(ProcessResult(
                    success=False,
                    source_path=sc_path,
                    dest_path=sc_dest,
                    bytes_copied=0,
                    is_sidecar=True,
                    error=str(exc),
                ))

        return results
