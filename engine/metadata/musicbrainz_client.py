"""Optional audio metadata enrichment via the MusicBrainz API."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import musicbrainzngs

if TYPE_CHECKING:
    from sortique.engine.metadata.audio_metadata import AudioMetadata

logger = logging.getLogger(__name__)


class MusicBrainzClient:
    """Optional audio metadata enrichment via MusicBrainz API.

    Disabled by default.  Must be explicitly enabled by user.
    """

    USER_AGENT = ("Sortique", "1.0", "https://github.com/sortique")
    RATE_LIMIT_SECONDS = 1.0  # MusicBrainz requires max 1 request/second
    MAX_CONSECUTIVE_FAILURES = 3
    MAX_BACKOFF_SECONDS = 60.0

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._last_request_time: float = 0.0
        self._consecutive_failures: int = 0
        self._unavailable: bool = False

        if enabled:
            musicbrainzngs.set_useragent(*self.USER_AGENT)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich(self, audio_meta: AudioMetadata, filepath: str) -> AudioMetadata:
        """Attempt to enrich *audio_meta* using MusicBrainz.

        If not enabled or unavailable, returns *audio_meta* unchanged.

        Strategy:

        1. If title and artist already present, search by recording:
           artist + title.
        2. If only title present, search by recording title.
        3. Parse response for: artist, album, genre.
        4. Only fill in fields that are currently ``None``.
        5. Never overwrite existing metadata.

        Rate limiting:

        - Enforce minimum :attr:`RATE_LIMIT_SECONDS` between API calls.
        - On 503 / network error: exponential backoff (1 s, 2 s, 4 s, …,
          max 60 s).
        - After 3 consecutive failures: mark as unavailable for the rest
          of the session.

        Error handling:

        - On any exception: log warning, return original *audio_meta*
          unchanged.
        - Never raise exceptions.
        - Never block the pipeline.
        """
        if not self.enabled or self._unavailable:
            return audio_meta

        # Need at least a title to search (title is now always set via filename fallback)
        if not audio_meta.title:
            return audio_meta

        try:
            result = self._search_recording(
                audio_meta.title, audio_meta.artist,
            )
        except Exception as exc:
            logger.warning("MusicBrainz search failed: %s", exc)
            return audio_meta

        if result is None:
            return audio_meta

        # --- fill in missing fields only ---
        if audio_meta.artist is None:
            artist = self._extract_artist(result)
            if artist:
                audio_meta.artist = artist
                audio_meta.has_tags = True

        if audio_meta.album is None:
            album = self._extract_album(result)
            if album:
                audio_meta.album = album
                audio_meta.has_tags = True

        if audio_meta.genre is None:
            genre = self._extract_genre(result)
            if genre:
                audio_meta.genre = genre

        if audio_meta.year is None:
            year = self._extract_year(result)
            if year:
                audio_meta.year = year

        return audio_meta

    @property
    def is_available(self) -> bool:
        """``False`` if the API has been marked unavailable due to
        repeated failures."""
        return not self._unavailable

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Sleep if needed to respect the MusicBrainz rate limit."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.RATE_LIMIT_SECONDS:
            time.sleep(self.RATE_LIMIT_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_recording(
        self, title: str, artist: str | None = None,
    ) -> dict | None:
        """Search MusicBrainz for a recording.

        Returns the best match dict, or ``None`` if nothing useful is
        found.  Handles rate limiting and exponential backoff internally.
        """
        self._rate_limit()

        try:
            if artist:
                response = musicbrainzngs.search_recordings(
                    recording=title, artist=artist, limit=1,
                )
            else:
                response = musicbrainzngs.search_recordings(
                    recording=title, limit=1,
                )
        except musicbrainzngs.WebServiceError as exc:
            self._handle_api_error(exc)
            return None

        # Reset failure counter on success.
        self._consecutive_failures = 0

        recordings = response.get("recording-list", [])
        if not recordings:
            return None

        best = recordings[0]

        # Only accept high-confidence matches (score >= 80).
        score = int(best.get("ext:score", 0))
        if score < 80:
            return None

        return best

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_artist(recording: dict) -> str | None:
        """Pull the first artist name from a recording result."""
        credit_list = recording.get("artist-credit", [])
        for credit in credit_list:
            if isinstance(credit, dict):
                artist = credit.get("artist", {})
                name = artist.get("name")
                if name:
                    return name
        return None

    @staticmethod
    def _extract_album(recording: dict) -> str | None:
        """Pull the first release (album) title from a recording result."""
        release_list = recording.get("release-list", [])
        for release in release_list:
            if isinstance(release, dict):
                title = release.get("title")
                if title:
                    return title
        return None

    @staticmethod
    def _extract_genre(recording: dict) -> str | None:
        """Pull the top tag (genre) from a recording result."""
        tag_list = recording.get("tag-list", [])
        if not tag_list:
            return None

        # Pick the tag with the highest count.
        best_tag = None
        best_count = -1
        for tag in tag_list:
            if not isinstance(tag, dict):
                continue
            name = tag.get("name")
            count = int(tag.get("count", 0))
            if name and count > best_count:
                best_tag = name
                best_count = count

        return best_tag

    @staticmethod
    def _extract_year(recording: dict) -> int | None:
        """Pull the release year from a recording result.
        
        Extracts year from the first release date found.
        """
        release_list = recording.get("release-list", [])
        for release in release_list:
            if not isinstance(release, dict):
                continue
            
            # Try to get release date
            date_str = release.get("date")
            if not date_str:
                continue
            
            # Parse year from date string (format: YYYY or YYYY-MM-DD)
            try:
                year_str = date_str.split("-")[0]
                year = int(year_str)
                # Sanity check
                if 1900 <= year <= 2100:
                    return year
            except (ValueError, IndexError):
                continue
        
        return None

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _handle_api_error(self, exc: Exception) -> None:
        """Track consecutive failures and mark unavailable after too many."""
        self._consecutive_failures += 1
        logger.warning(
            "MusicBrainz API error (%d/%d): %s",
            self._consecutive_failures,
            self.MAX_CONSECUTIVE_FAILURES,
            exc,
        )

        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            self._unavailable = True
            logger.warning(
                "MusicBrainz marked unavailable after %d consecutive failures",
                self._consecutive_failures,
            )

        # Exponential backoff: 1s, 2s, 4s, 8s, … capped at MAX_BACKOFF_SECONDS.
        backoff = min(
            2 ** (self._consecutive_failures - 1),
            self.MAX_BACKOFF_SECONDS,
        )
        time.sleep(backoff)
