"""Tests for sortique.engine.metadata.musicbrainz_client."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from sortique.engine.metadata.audio_metadata import AudioMetadata
from sortique.engine.metadata.musicbrainz_client import MusicBrainzClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recording(
    title: str = "Test Song",
    artist: str = "Test Artist",
    album: str = "Test Album",
    genre: str | None = None,
    score: int = 95,
) -> dict:
    """Build a MusicBrainz recording result dict."""
    rec: dict = {
        "title": title,
        "ext:score": str(score),
        "artist-credit": [
            {"artist": {"name": artist}},
        ],
        "release-list": [
            {"title": album},
        ],
    }
    if genre is not None:
        rec["tag-list"] = [{"name": genre, "count": "5"}]
    return rec


def _search_response(*recordings: dict) -> dict:
    """Wrap recordings in a search response dict."""
    return {"recording-list": list(recordings)}


# ---------------------------------------------------------------------------
# Disabled / not enabled
# ---------------------------------------------------------------------------

class TestDisabled:
    """When disabled, enrich() should return the input unchanged."""

    def test_disabled_by_default(self):
        client = MusicBrainzClient()
        assert client.enabled is False

    def test_disabled_returns_unchanged(self):
        client = MusicBrainzClient(enabled=False)
        meta = AudioMetadata(title="Song", artist="Artist")
        result = client.enrich(meta, "/fake.mp3")
        assert result is meta
        assert result.title == "Song"
        assert result.artist == "Artist"

    def test_disabled_does_not_call_api(self):
        client = MusicBrainzClient(enabled=False)
        meta = AudioMetadata(title="Song")
        with patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs") as mock_mb:
            client.enrich(meta, "/fake.mp3")
            mock_mb.search_recordings.assert_not_called()

    def test_is_available_when_disabled(self):
        client = MusicBrainzClient(enabled=False)
        assert client.is_available is True


# ---------------------------------------------------------------------------
# Enrichment — fill missing fields
# ---------------------------------------------------------------------------

class TestEnrichment:
    """Test that enrich() fills in missing fields without overwriting."""

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_fills_missing_artist(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response(
            _recording(artist="Found Artist"),
        )
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="My Song", artist=None)

        result = client.enrich(meta, "/f.mp3")

        assert result.artist == "Found Artist"
        mock_mb.search_recordings.assert_called_once()

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_fills_missing_album(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response(
            _recording(album="Found Album"),
        )
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song", artist="Artist", album=None)

        result = client.enrich(meta, "/f.mp3")

        assert result.album == "Found Album"

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_fills_missing_genre(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response(
            _recording(genre="rock"),
        )
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song", genre=None)

        result = client.enrich(meta, "/f.mp3")

        assert result.genre == "rock"

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_does_not_overwrite_existing_artist(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response(
            _recording(artist="Wrong Artist"),
        )
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song", artist="Original Artist")

        result = client.enrich(meta, "/f.mp3")

        assert result.artist == "Original Artist"

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_does_not_overwrite_existing_album(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response(
            _recording(album="Wrong Album"),
        )
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(
            title="Song", artist="A", album="My Album",
        )

        result = client.enrich(meta, "/f.mp3")

        assert result.album == "My Album"

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_does_not_overwrite_existing_genre(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response(
            _recording(genre="pop"),
        )
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song", genre="jazz")

        result = client.enrich(meta, "/f.mp3")

        assert result.genre == "jazz"

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_has_tags_updated_on_enrichment(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response(
            _recording(artist="Found"),
        )
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song", has_tags=False)

        result = client.enrich(meta, "/f.mp3")

        assert result.has_tags is True

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_no_title_skips_search(self, mock_mb):
        """Without a title there is nothing to search for."""
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title=None, artist="Artist")

        result = client.enrich(meta, "/f.mp3")

        assert result is meta
        mock_mb.search_recordings.assert_not_called()


# ---------------------------------------------------------------------------
# Search strategy
# ---------------------------------------------------------------------------

class TestSearchStrategy:
    """Verify the correct search parameters are sent."""

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_search_with_artist_and_title(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response()
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Hello", artist="Adele")

        client.enrich(meta, "/f.mp3")

        mock_mb.search_recordings.assert_called_once_with(
            recording="Hello", artist="Adele", limit=1,
        )

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_search_title_only(self, mock_mb):
        mock_mb.search_recordings.return_value = _search_response()
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Hello")

        client.enrich(meta, "/f.mp3")

        mock_mb.search_recordings.assert_called_once_with(
            recording="Hello", limit=1,
        )

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_low_score_rejected(self, mock_mb):
        """Matches below 80 should be discarded."""
        mock_mb.search_recordings.return_value = _search_response(
            _recording(artist="Weak Match", score=50),
        )
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song", artist=None)

        result = client.enrich(meta, "/f.mp3")

        # Should NOT have filled in the artist due to low score.
        assert result.artist is None

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_empty_recording_list(self, mock_mb):
        mock_mb.search_recordings.return_value = {"recording-list": []}
        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Obscure Song")

        result = client.enrich(meta, "/f.mp3")

        assert result is meta


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """Verify rate-limiting behavior."""

    @patch("sortique.engine.metadata.musicbrainz_client.time")
    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_rate_limit_sleeps_when_too_fast(self, mock_mb, mock_time):
        mock_mb.search_recordings.return_value = _search_response()
        mock_time.monotonic.side_effect = [
            # First call to _rate_limit: "now" is 0.0
            0.0,
            # After sleep, update _last_request_time
            1.0,
            # Second call to _rate_limit: "now" is 1.3 (only 0.3s elapsed)
            1.3,
            # After sleep, update _last_request_time
            2.0,
        ]

        client = MusicBrainzClient(enabled=True)
        client._last_request_time = 0.0

        meta1 = AudioMetadata(title="Song 1")
        meta2 = AudioMetadata(title="Song 2")

        client.enrich(meta1, "/f.mp3")
        client.enrich(meta2, "/f.mp3")

        # Should have slept on the second call (0.7s needed)
        calls = mock_time.sleep.call_args_list
        assert len(calls) >= 1
        # The second _rate_limit call should sleep
        # (RATE_LIMIT_SECONDS - elapsed) = 1.0 - 0.3 = 0.7
        slept = calls[-1][0][0]
        assert 0.6 < slept < 0.8

    @patch("sortique.engine.metadata.musicbrainz_client.time")
    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_rate_limit_no_sleep_when_enough_time_passed(
        self, mock_mb, mock_time,
    ):
        mock_mb.search_recordings.return_value = _search_response()
        # monotonic returns: first _rate_limit "now" far after last request
        mock_time.monotonic.side_effect = [100.0, 100.0]

        client = MusicBrainzClient(enabled=True)
        client._last_request_time = 0.0  # long ago

        client.enrich(AudioMetadata(title="Song"), "/f.mp3")

        mock_time.sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Error handling / availability
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """API errors should never propagate and should degrade gracefully."""

    @patch("sortique.engine.metadata.musicbrainz_client.time")
    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_api_error_returns_unchanged(self, mock_mb, mock_time):
        mock_time.monotonic.return_value = 100.0
        mock_mb.WebServiceError = type("WebServiceError", (Exception,), {})
        mock_mb.search_recordings.side_effect = mock_mb.WebServiceError(
            "503 Service Unavailable",
        )

        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song", artist=None)

        result = client.enrich(meta, "/f.mp3")

        assert result is meta
        assert result.artist is None

    @patch("sortique.engine.metadata.musicbrainz_client.time")
    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_unavailable_after_consecutive_failures(self, mock_mb, mock_time):
        mock_time.monotonic.return_value = 100.0
        mock_mb.WebServiceError = type("WebServiceError", (Exception,), {})
        mock_mb.search_recordings.side_effect = mock_mb.WebServiceError(
            "503",
        )

        client = MusicBrainzClient(enabled=True)
        assert client.is_available is True

        meta = AudioMetadata(title="Song")
        for _ in range(MusicBrainzClient.MAX_CONSECUTIVE_FAILURES):
            client.enrich(meta, "/f.mp3")

        assert client.is_available is False

    @patch("sortique.engine.metadata.musicbrainz_client.time")
    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_unavailable_skips_further_calls(self, mock_mb, mock_time):
        mock_time.monotonic.return_value = 100.0
        mock_mb.WebServiceError = type("WebServiceError", (Exception,), {})
        mock_mb.search_recordings.side_effect = mock_mb.WebServiceError(
            "503",
        )

        client = MusicBrainzClient(enabled=True)

        meta = AudioMetadata(title="Song")
        for _ in range(MusicBrainzClient.MAX_CONSECUTIVE_FAILURES):
            client.enrich(meta, "/f.mp3")

        # Reset mock to track new calls.
        mock_mb.search_recordings.reset_mock()

        # Further calls should not even attempt the API.
        client.enrich(meta, "/f.mp3")
        mock_mb.search_recordings.assert_not_called()

    @patch("sortique.engine.metadata.musicbrainz_client.time")
    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_exponential_backoff(self, mock_mb, mock_time):
        mock_time.monotonic.return_value = 100.0
        mock_mb.WebServiceError = type("WebServiceError", (Exception,), {})
        mock_mb.search_recordings.side_effect = mock_mb.WebServiceError(
            "503",
        )

        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song")

        # First failure → backoff 1s
        client.enrich(meta, "/f.mp3")
        first_backoff = mock_time.sleep.call_args_list[-1][0][0]
        assert first_backoff == 1.0

        # Second failure → backoff 2s
        client.enrich(meta, "/f.mp3")
        second_backoff = mock_time.sleep.call_args_list[-1][0][0]
        assert second_backoff == 2.0

    @patch("sortique.engine.metadata.musicbrainz_client.time")
    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_success_resets_failure_counter(self, mock_mb, mock_time):
        mock_time.monotonic.return_value = 100.0
        mock_mb.WebServiceError = type("WebServiceError", (Exception,), {})

        # First call fails, second succeeds.
        mock_mb.search_recordings.side_effect = [
            mock_mb.WebServiceError("503"),
            _search_response(_recording()),
        ]

        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song")

        client.enrich(meta, "/f.mp3")  # fails
        assert client._consecutive_failures == 1

        client.enrich(meta, "/f.mp3")  # succeeds
        assert client._consecutive_failures == 0

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_generic_exception_returns_unchanged(self, mock_mb):
        """Non-API exceptions (network error, etc.) are also caught."""
        mock_mb.search_recordings.side_effect = ConnectionError("timeout")

        client = MusicBrainzClient(enabled=True)
        meta = AudioMetadata(title="Song", artist=None)

        result = client.enrich(meta, "/f.mp3")

        assert result is meta
        assert result.artist is None


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

class TestParsing:
    """Test the static extraction methods."""

    def test_extract_artist(self):
        rec = {
            "artist-credit": [
                {"artist": {"name": "Freddie Mercury"}},
            ],
        }
        assert MusicBrainzClient._extract_artist(rec) == "Freddie Mercury"

    def test_extract_artist_empty(self):
        assert MusicBrainzClient._extract_artist({}) is None
        assert MusicBrainzClient._extract_artist(
            {"artist-credit": []},
        ) is None

    def test_extract_album(self):
        rec = {
            "release-list": [
                {"title": "A Night at the Opera"},
            ],
        }
        assert MusicBrainzClient._extract_album(rec) == "A Night at the Opera"

    def test_extract_album_empty(self):
        assert MusicBrainzClient._extract_album({}) is None

    def test_extract_genre_picks_highest_count(self):
        rec = {
            "tag-list": [
                {"name": "pop", "count": "2"},
                {"name": "rock", "count": "10"},
                {"name": "alternative", "count": "5"},
            ],
        }
        assert MusicBrainzClient._extract_genre(rec) == "rock"

    def test_extract_genre_empty(self):
        assert MusicBrainzClient._extract_genre({}) is None
        assert MusicBrainzClient._extract_genre(
            {"tag-list": []},
        ) is None

    def test_extract_artist_skips_non_dict(self):
        """Artist-credit may contain plain strings (join phrases)."""
        rec = {
            "artist-credit": [
                " & ",
                {"artist": {"name": "Brian May"}},
            ],
        }
        assert MusicBrainzClient._extract_artist(rec) == "Brian May"


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_enabled_sets_user_agent(self, mock_mb):
        MusicBrainzClient(enabled=True)
        mock_mb.set_useragent.assert_called_once_with(
            *MusicBrainzClient.USER_AGENT,
        )

    @patch("sortique.engine.metadata.musicbrainz_client.musicbrainzngs")
    def test_disabled_does_not_set_user_agent(self, mock_mb):
        MusicBrainzClient(enabled=False)
        mock_mb.set_useragent.assert_not_called()
