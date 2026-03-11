"""Tests for sortique.engine.categorizer."""

from __future__ import annotations

from datetime import datetime

import pytest

from sortique.data.config_manager import ConfigManager
from sortique.engine.categorizer import Categorizer, _DISPLAY_RATIOS, _RAW_FORMATS
from sortique.engine.metadata.audio_metadata import AudioMetadata
from sortique.engine.metadata.exif_extractor import ExifResult
from sortique.engine.metadata.video_metadata import VideoMetadata


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(tmp_path):
    return ConfigManager(config_dir=str(tmp_path / "cfg"))


@pytest.fixture()
def cat(config):
    return Categorizer(config)


def _exif(**kw) -> ExifResult:
    """Shorthand ExifResult builder — any field not given gets its default."""
    return ExifResult(**kw)


def _video(**kw) -> VideoMetadata:
    return VideoMetadata(**kw)


def _audio(**kw) -> AudioMetadata:
    return AudioMetadata(**kw)


# ===========================================================================
# 1.  Image — individual categories
# ===========================================================================

class TestImageRAW:
    """Priority 1: RAW format detection."""

    @pytest.mark.parametrize("detail", ["cr2", "CR2", "nef", "ARW", "dng", "DNG", "orf"])
    def test_raw_formats(self, cat, detail):
        assert cat.categorize_image("/photos/IMG_001.cr2", _exif(), detail) == "RAW"

    def test_raw_beats_everything(self, cat):
        """Even with editor software + camera make, RAW wins."""
        exif = _exif(
            make="Canon", software="Adobe Lightroom",
            date_original=datetime(2024, 1, 1),
        )
        assert cat.categorize_image("/photos/IMG.dng", exif, "dng") == "RAW"

    def test_non_raw_not_flagged(self, cat):
        assert cat.categorize_image("/photos/img.jpg", _exif(), "jpeg") != "RAW"


class TestImageEdited:
    """Priority 2: Editor software detected."""

    @pytest.mark.parametrize("sw", [
        "Adobe Photoshop CC 2024",
        "Adobe Lightroom 6.0",
        "GIMP 2.10.36",
        "Snapseed 2.21",
        "Google Photos",
        "Instagram",
        "VSCO 2.0",
    ])
    def test_editor_patterns(self, cat, sw):
        exif = _exif(software=sw)
        assert cat.categorize_image("/photos/edit.jpg", exif, "jpeg") == "Edited"

    def test_editor_exclusion(self, cat, config):
        """Software matching an exclusion pattern should NOT be flagged Edited."""
        config.set("editor_exclusions", ["Google Photos"])
        cat_ex = Categorizer(config)
        exif = _exif(software="Google Photos 6.24")
        assert cat_ex.categorize_image("/p/img.jpg", exif, "jpeg") != "Edited"

    def test_no_software(self, cat):
        assert cat.categorize_image("/p/img.jpg", _exif(), "jpeg") != "Edited"

    def test_unknown_software(self, cat):
        exif = _exif(software="My Custom App")
        assert cat.categorize_image("/p/img.jpg", exif, "jpeg") != "Edited"


class TestImageScreenshots:
    """Priority 3: Screenshots by filename or resolution."""

    # --- filename patterns ---
    def test_screenshot_filename(self, cat):
        exif = _exif()
        assert cat.categorize_image("/p/Screenshot_20240315.png", exif, "png") == "Screenshots"

    def test_scr_filename(self, cat):
        exif = _exif()
        assert cat.categorize_image("/p/SCR_001.png", exif, "png") == "Screenshots"

    # --- resolution match ---
    def test_known_resolution_1080x1920(self, cat):
        """Exact match: iPhone 6/7/8 Plus resolution."""
        exif = _exif(width=1080, height=1920)
        assert cat.categorize_image("/p/img.png", exif, "png") == "Screenshots"

    def test_known_resolution_within_tolerance(self, cat):
        """1080±10 × 1920±10 should still match."""
        exif = _exif(width=1085, height=1915)
        assert cat.categorize_image("/p/img.png", exif, "png") == "Screenshots"

    def test_known_resolution_rotated(self, cat):
        """1920x1080 = rotated 1080x1920."""
        exif = _exif(width=1920, height=1080)
        assert cat.categorize_image("/p/img.png", exif, "png") == "Screenshots"

    def test_screenshot_res_blocked_by_camera(self, cat):
        """Phone resolution with camera make → NOT a screenshot."""
        exif = _exif(width=1080, height=1920, make="Samsung")
        assert cat.categorize_image("/p/img.png", exif, "png") != "Screenshots"

    def test_screenshot_res_blocked_by_gps(self, cat):
        """Phone resolution with GPS → NOT a screenshot."""
        exif = _exif(width=1080, height=1920, gps_lat=40.0)
        assert cat.categorize_image("/p/img.png", exif, "png") != "Screenshots"

    # --- display heuristic ---
    def test_display_heuristic_16_9(self, cat):
        """1920×1080 = 120 × (16:9) — classic HD."""
        exif = _exif(width=1920, height=1080)
        assert cat.categorize_image("/p/img.png", exif, "png") == "Screenshots"

    def test_display_heuristic_4_3(self, cat):
        """2048×1536 = 512 × (4:3) — iPad resolution."""
        exif = _exif(width=2048, height=1536)
        assert cat.categorize_image("/p/img.png", exif, "png") == "Screenshots"

    def test_display_heuristic_9_19_5(self, cat):
        """1440×3120 = 80 × (18:39)  (9:19.5 phone)."""
        exif = _exif(width=1440, height=3120)
        assert cat.categorize_image("/p/img.png", exif, "png") == "Screenshots"

    def test_display_heuristic_blocked_by_camera(self, cat):
        exif = _exif(width=1920, height=1080, make="Nikon")
        assert cat.categorize_image("/p/img.jpg", exif, "jpeg") != "Screenshots"


class TestImageSocialMedia:
    """Priority 4: Social-media filename patterns."""

    @pytest.mark.parametrize("name", [
        "IMG-20240315-WA0001.jpg",
        "FB_IMG_1710000000.jpg",
        "received_123456789.jpg",
        # UUID-format filename (e.g. downloaded from social apps)
        "aba82dfd-60ee-4a20-81ee-e160c7f01c4a.jpg",
        "0c7cbc5e-ddf8-4f18-8ee8-c0a9aeb6d402.jpg",
        # WA with edited suffix
        "IMG-20201126-WA0003-edited.jpg",
    ])
    def test_social_media_patterns(self, cat, name):
        exif = _exif()
        assert cat.categorize_image(f"/p/{name}", exif, "jpeg") == "Social Media"

    def test_uuid_beats_screenshot_resolution(self, cat):
        """UUID-named file with phone resolution → Social Media, not Screenshots."""
        exif = _exif(width=1080, height=1920)
        assert cat.categorize_image(
            "/p/aba82dfd-60ee-4a20-81ee-e160c7f01c4a.jpg", exif, "jpeg",
        ) == "Social Media"

    def test_wa_filename_beats_screenshot_resolution(self, cat):
        """WhatsApp image with phone resolution → Social Media, not Screenshots."""
        exif = _exif(width=1080, height=1920)
        assert cat.categorize_image(
            "/p/IMG-20201126-WA0003.jpg", exif, "jpeg",
        ) == "Social Media"

    def test_non_matching_name(self, cat):
        exif = _exif()
        assert cat.categorize_image("/p/DSC_0001.jpg", exif, "jpeg") != "Social Media"


class TestImageHidden:
    """Priority 5: Sidecar extensions."""

    @pytest.mark.parametrize("ext", [".aae", ".xmp", ".thm", ".srt"])
    def test_sidecar_extensions(self, cat, ext):
        exif = _exif()
        assert cat.categorize_image(f"/p/IMG_001{ext}", exif, "jpeg") == "Hidden"


class TestImageOriginals:
    """Priority 6: Has EXIF camera make."""

    def test_camera_make_present(self, cat):
        exif = _exif(make="Canon")
        assert cat.categorize_image("/p/IMG_001.jpg", exif, "jpeg") == "Originals"

    def test_camera_make_with_gps(self, cat):
        """Camera make present → Originals even with GPS."""
        exif = _exif(make="Nikon", gps_lat=48.8, gps_lon=2.3)
        assert cat.categorize_image("/p/IMG_001.jpg", exif, "jpeg") == "Originals"


class TestImageExport:
    """Priority 7: Has date but no camera make."""

    def test_date_original_only(self, cat):
        exif = _exif(date_original=datetime(2024, 3, 15))
        assert cat.categorize_image("/p/IMG_001.jpg", exif, "jpeg") == "Export"

    def test_date_digitized_only(self, cat):
        exif = _exif(date_digitized=datetime(2024, 3, 15))
        assert cat.categorize_image("/p/IMG_001.jpg", exif, "jpeg") == "Export"

    def test_date_modified_only(self, cat):
        exif = _exif(date_modified=datetime(2024, 3, 15))
        assert cat.categorize_image("/p/IMG_001.jpg", exif, "jpeg") == "Export"


class TestImageCollection:
    """Priority 8: Fallback — no metadata at all."""

    def test_bare_file(self, cat):
        assert cat.categorize_image("/p/IMG_001.jpg", _exif(), "jpeg") == "Collection"

    def test_unknown_extension(self, cat):
        assert cat.categorize_image("/p/mystery.dat", _exif(), "unknown") == "Collection"


# ===========================================================================
# 2.  Image priority ordering
# ===========================================================================

class TestImagePriorityOrder:
    """Verify that higher-priority rules win over lower ones."""

    def test_raw_beats_edited(self, cat):
        exif = _exif(software="Adobe Photoshop")
        assert cat.categorize_image("/p/img.cr2", exif, "cr2") == "RAW"

    def test_edited_beats_screenshot_filename(self, cat):
        exif = _exif(software="Adobe Photoshop")
        assert cat.categorize_image("/p/Screenshot_001.png", exif, "png") == "Edited"

    def test_edited_beats_social_media(self, cat):
        exif = _exif(software="GIMP 2.10")
        assert cat.categorize_image("/p/IMG-20240315-WA0001.jpg", exif, "jpeg") == "Edited"

    def test_screenshot_filename_beats_social_media(self, cat):
        """Screenshot filename pattern fires before social-media pattern."""
        assert cat.categorize_image(
            "/p/Screenshot_001.png", _exif(), "png",
        ) == "Screenshots"

    def test_social_media_beats_screenshot_resolution(self, cat):
        """Social-media filename matches before resolution heuristic fires."""
        exif = _exif(width=1080, height=1920)
        assert cat.categorize_image(
            "/p/IMG-20240315-WA0001.jpg", exif, "jpeg",
        ) == "Social Media"

    def test_social_media_beats_originals(self, cat):
        exif = _exif(make="Apple")
        assert cat.categorize_image(
            "/p/IMG-20240315-WA0001.jpg", exif, "jpeg",
        ) == "Social Media"

    def test_originals_beats_export(self, cat):
        exif = _exif(make="Sony", date_original=datetime(2024, 3, 15))
        assert cat.categorize_image("/p/img.jpg", exif, "jpeg") == "Originals"

    def test_export_beats_collection(self, cat):
        exif = _exif(date_original=datetime(2024, 3, 15))
        assert cat.categorize_image("/p/img.jpg", exif, "jpeg") == "Export"


# ===========================================================================
# 3.  Video categorisation
# ===========================================================================

class TestVideoMotionPhoto:
    """Priority 1: Short clip + motion filename pattern."""

    def test_motion_photo_short_clip(self, cat):
        meta = _video(duration_seconds=3.0)
        assert cat.categorize_video("/p/IMG_20240315_MVIMG_001.mp4", meta) == "Motion Photos"

    def test_motion_photo_pattern_motion_prefix(self, cat):
        meta = _video(duration_seconds=5.0)
        assert cat.categorize_video("/p/MOTION_001.mp4", meta) == "Motion Photos"

    def test_too_long_not_motion(self, cat):
        meta = _video(duration_seconds=15.0)
        assert cat.categorize_video("/p/IMG_20240315_MVIMG_001.mp4", meta) != "Motion Photos"

    def test_duration_unknown_skips_motion(self, cat):
        meta = _video(duration_unknown=True)
        assert cat.categorize_video("/p/IMG_20240315_MVIMG_001.mp4", meta) != "Motion Photos"

    def test_no_pattern_match_not_motion(self, cat):
        meta = _video(duration_seconds=3.0)
        assert cat.categorize_video("/p/regular_clip.mp4", meta) != "Motion Photos"


class TestVideoSocialMedia:
    """Priority 2: Social-media video filename patterns."""

    @pytest.mark.parametrize("name", [
        "VID-20240315-WA0001.mp4",
        "FB_VID_20240315.mp4",
    ])
    def test_social_media_video(self, cat, name):
        meta = _video()
        assert cat.categorize_video(f"/p/{name}", meta) == "Social Media"


class TestVideoCamera:
    """Priority 3: Has make / model metadata → Camera."""

    def test_with_make_and_model(self, cat):
        meta = _video(make="Apple", model="iPhone 15")
        assert cat.categorize_video("/p/clip.mp4", meta) == "Camera"

    def test_with_model_only(self, cat):
        meta = _video(model="GoPro HERO12")
        assert cat.categorize_video("/p/clip.mp4", meta) == "Camera"


class TestVideoMobile:
    """Priority 4: GPS / old mobile formats → Mobile."""

    def test_has_location(self, cat):
        """Android camera recording with GPS but no make/model → Mobile."""
        meta = _video(has_location=True)
        assert cat.categorize_video("/p/clip.mp4", meta) == "Mobile"

    def test_3gp_extension(self, cat):
        """Old Nokia/Sony Ericsson 3GP recording → Mobile."""
        meta = _video(duration_seconds=60.0)
        assert cat.categorize_video("/p/clip.3gp", meta) == "Mobile"

    def test_3g2_extension(self, cat):
        meta = _video(duration_seconds=60.0)
        assert cat.categorize_video("/p/clip.3g2", meta) == "Mobile"

    def test_location_beats_movies(self, cat):
        """Long Android recording with GPS → Mobile, not Movies."""
        meta = _video(has_location=True, duration_seconds=2000.0)
        assert cat.categorize_video("/p/clip.mp4", meta) == "Mobile"


class TestVideoCamcorder:
    """Priority 5: Dedicated recording-device formats → Camcorder."""

    @pytest.mark.parametrize("ext", [".mts", ".m2ts", ".mod", ".tod", ".mxf"])
    def test_camcorder_extensions(self, cat, ext):
        meta = _video(duration_seconds=300.0)
        assert cat.categorize_video(f"/p/clip{ext}", meta) == "Camcorder"

    def test_camcorder_beats_movies(self, cat):
        """Long MTS recording → Camcorder, not Movies."""
        meta = _video(duration_seconds=2000.0)
        assert cat.categorize_video("/p/clip.mts", meta) == "Camcorder"


class TestVideoMovies:
    """Priority 6: Long video (> 15 min / 900 s), no camera signal."""

    def test_long_video(self, cat):
        meta = _video(duration_seconds=1800.0)
        assert cat.categorize_video("/p/movie.mp4", meta) == "Movies"

    def test_exactly_900_not_movie(self, cat):
        """900 s is NOT > 900."""
        meta = _video(duration_seconds=900.0)
        assert cat.categorize_video("/p/clip.mp4", meta) != "Movies"

    def test_901_is_movie(self, cat):
        meta = _video(duration_seconds=901.0)
        assert cat.categorize_video("/p/clip.mp4", meta) == "Movies"

    def test_duration_unknown_skips_movie_check(self, cat):
        meta = _video(duration_unknown=True)
        assert cat.categorize_video("/p/long_movie.mp4", meta) == "Clips"


class TestVideoClips:
    """Priority 7: Clips catch-all."""

    def test_plain_video(self, cat):
        meta = _video(duration_seconds=60.0)
        assert cat.categorize_video("/p/clip.mp4", meta) == "Clips"

    def test_no_metadata_at_all(self, cat):
        meta = _video(duration_unknown=True)
        assert cat.categorize_video("/p/clip.mp4", meta) == "Clips"


class TestVideoPriorityOrder:
    """Cross-priority checks."""

    def test_motion_beats_social_media(self, cat):
        """A short MVIMG WhatsApp video → Motion Photos (rule 1 before 2)."""
        meta = _video(duration_seconds=2.0)
        # Filename matches both MVIMG and WA patterns — motion wins.
        assert cat.categorize_video(
            "/p/VID_20240315_MVIMG_WA001.mp4", meta,
        ) == "Motion Photos"

    def test_social_media_beats_camera(self, cat):
        meta = _video(make="Apple", model="iPhone 15")
        assert cat.categorize_video(
            "/p/VID-20240315-WA0001.mp4", meta,
        ) == "Social Media"

    def test_camera_beats_movies(self, cat):
        """Camera make present → Camera even if duration > 15 min."""
        meta = _video(make="Sony", duration_seconds=2000.0)
        assert cat.categorize_video("/p/clip.mp4", meta) == "Camera"


# ===========================================================================
# 4.  Audio categorisation
# ===========================================================================

class TestAudioVoiceNotes:
    """Priority 1: Voice-note extension + filename pattern."""

    @pytest.mark.parametrize("name,ext", [
        ("Recording_001.m4a", ".m4a"),
        ("Voice_20240315.aac", ".aac"),
        ("Audio_note.amr", ".amr"),
    ])
    def test_voice_note_patterns(self, cat, name, ext):
        meta = _audio()
        assert cat.categorize_audio(f"/p/{name}", meta) == "Voice Notes"

    def test_wrong_extension(self, cat):
        """MP3 is not a voice-note extension even with matching filename."""
        meta = _audio()
        assert cat.categorize_audio("/p/Recording_001.mp3", meta) != "Voice Notes"

    def test_wrong_pattern(self, cat):
        """Correct extension but non-matching filename."""
        meta = _audio()
        assert cat.categorize_audio("/p/song.m4a", meta) != "Voice Notes"


class TestAudioWhatsApp:
    """Priority 2: WhatsApp voice messages."""

    def test_opus_whatsapp(self, cat):
        meta = _audio()
        assert cat.categorize_audio("/p/PTT-20240315-WA0001.opus", meta) == "WhatsApp"

    def test_ogg_whatsapp(self, cat):
        meta = _audio()
        assert cat.categorize_audio("/p/PTT-20240315-WA0002.ogg", meta) == "WhatsApp"

    def test_wrong_extension(self, cat):
        meta = _audio()
        assert cat.categorize_audio("/p/PTT-20240315-WA0001.mp3", meta) != "WhatsApp"

    def test_wrong_pattern(self, cat):
        meta = _audio()
        assert cat.categorize_audio("/p/voice_message.opus", meta) != "WhatsApp"


class TestAudioCallRecordings:
    """Priority 3: Call recording filename patterns."""

    @pytest.mark.parametrize("name", [
        "SIM2_20161225_2003.wav",
        "SIM1_20240101_120000.mp3",
        "Call_20240315_123456.m4a",   # Call_[digit]*
        "callrecord_20240101.mp3",
        # Samsung call recorder: ContactName_PhoneNumber_YYYYMMDD_HHMMSS.m4a
        "JohnDoe_1234567890_20240311_103000.m4a",
        "Unknown_9876543210_20240315_140530.m4a",
        "Mom_441234567890_20240101_090000.m4a",   # with country code digits
    ])
    def test_call_recording_patterns(self, cat, name):
        meta = _audio()
        assert cat.categorize_audio(f"/p/{name}", meta) == "Call Recordings"

    def test_not_call_recording_untagged(self, cat):
        """Generic audio file not matching call pattern → Songs (music ext) or Collection."""
        meta = _audio()
        # .wav → Songs (music-extension fallback), not Call Recordings
        assert cat.categorize_audio("/p/audio_note.wav", meta) != "Call Recordings"

    def test_call_recording_any_extension(self, cat):
        """Call recording patterns are extension-agnostic."""
        meta = _audio(has_tags=True)
        # Even with tags, SIM* pattern wins over Songs.
        assert cat.categorize_audio("/p/SIM2_20161225_2003.wav", meta) == "Call Recordings"

    def test_record_prefix_not_call_recording(self, cat):
        """'record_*' was removed — files like record_01.mp3 go to Songs."""
        meta = _audio()
        result = cat.categorize_audio("/p/record_01.mp3", meta)
        assert result == "Songs"   # music-extension fallback, NOT Call Recordings

    def test_incoming_prefix_not_call_recording(self, cat):
        """'incoming_*' was removed to avoid catching 'incoming_tide.mp3' etc."""
        meta = _audio()
        result = cat.categorize_audio("/p/incoming_20240315.wav", meta)
        assert result == "Songs"   # music-extension fallback, NOT Call Recordings


class TestAudioSongs:
    """Priority 4: has_tags is True."""

    def test_tagged_audio(self, cat):
        meta = _audio(has_tags=True, title="My Song", artist="Band")
        assert cat.categorize_audio("/p/track.mp3", meta) == "Songs"

    def test_has_tags_true_no_fields(self, cat):
        meta = _audio(has_tags=True)
        assert cat.categorize_audio("/p/track.flac", meta) == "Songs"

    def test_tagged_m4a(self, cat):
        """Tagged .m4a not matching voice pattern → Songs."""
        meta = _audio(has_tags=True, title="Purchased Track")
        assert cat.categorize_audio("/p/purchased.m4a", meta) == "Songs"


class TestAudioMusicExtension:
    """Priority 5: Untagged music formats still go to Songs."""

    @pytest.mark.parametrize("name,ext", [
        ("track_01.mp3", "mp3"),
        ("symphony.flac", "flac"),
        ("oldfile.wma", "wma"),
        ("cd_rip.aiff", "aiff"),
        ("cd_rip.aif", "aif"),
        ("lossless.ape", "ape"),
        ("wave_music.wav", "wav"),
    ])
    def test_music_extension_fallback(self, cat, name, ext):
        """Untagged files with music extensions → Songs (not Collection)."""
        meta = _audio()  # has_tags = False
        assert cat.categorize_audio(f"/p/{name}", meta) == "Songs"

    def test_untagged_ogg_not_songs(self, cat):
        """Untagged .ogg that's not WhatsApp → Collection (ambiguous format)."""
        meta = _audio()
        assert cat.categorize_audio("/p/voice_message.ogg", meta) == "Collection"

    def test_untagged_amr_not_songs(self, cat):
        """Untagged .amr that's not voice-note → Collection (ambiguous format)."""
        meta = _audio()
        assert cat.categorize_audio("/p/memo.amr", meta) == "Collection"


class TestAudioCollection:
    """Priority 6: Fallback for truly ambiguous audio."""

    def test_untagged_ogg(self, cat):
        meta = _audio()
        assert cat.categorize_audio("/p/unknown.ogg", meta) == "Collection"

    def test_untagged_opus(self, cat):
        """Untagged .opus not matching WhatsApp pattern → Collection."""
        meta = _audio()
        assert cat.categorize_audio("/p/unknown.opus", meta) == "Collection"


class TestAudioPriorityOrder:

    def test_voice_beats_songs(self, cat):
        """Voice-note pattern + has_tags → Voice Notes (rule 1)."""
        meta = _audio(has_tags=True, title="My Voice Memo")
        assert cat.categorize_audio("/p/Recording_001.m4a", meta) == "Voice Notes"

    def test_whatsapp_beats_songs(self, cat):
        meta = _audio(has_tags=True)
        assert cat.categorize_audio("/p/PTT-20240315-WA0001.opus", meta) == "WhatsApp"

    def test_call_recordings_beats_songs(self, cat):
        """Call recording pattern + has_tags → Call Recordings (rule 3 before 4)."""
        meta = _audio(has_tags=True)
        assert cat.categorize_audio("/p/SIM2_20161225_2003.wav", meta) == "Call Recordings"


# ===========================================================================
# 5.  Document categorisation
# ===========================================================================

class TestDocumentCategories:

    @pytest.mark.parametrize("ext,expected", [
        (".pdf", "Documents/PDF"),
        (".txt", "Documents/Text"),
        (".md", "Documents/Text"),
        (".rtf", "Documents/Text"),
        (".doc", "Documents/Word"),
        (".docx", "Documents/Word"),
        (".xls", "Documents/Excel"),
        (".xlsx", "Documents/Excel"),
        (".ppt", "Documents/PowerPoint"),
        (".pptx", "Documents/PowerPoint"),
        (".py", "Documents/Code"),
        (".js", "Documents/Code"),
        (".java", "Documents/Code"),
        (".html", "Documents/Code"),
        (".json", "Documents/Code"),
        (".yaml", "Documents/Code"),
        (".css", "Documents/Code"),
        (".csv", "Documents/Others"),
        (".epub", "Documents/Others"),
        (".odt", "Documents/Others"),
        (".log", "Documents/Others"),
    ])
    def test_extension_mapping(self, cat, ext, expected):
        assert cat.categorize_document(f"/docs/file{ext}") == expected

    def test_case_insensitive_extension(self, cat):
        assert cat.categorize_document("/docs/file.PDF") == "Documents/PDF"
        assert cat.categorize_document("/docs/file.Docx") == "Documents/Word"

    def test_unknown_extension(self, cat):
        assert cat.categorize_document("/docs/file.xyz") == "Documents/Others"


# ===========================================================================
# 6.  Helper methods
# ===========================================================================

class TestMatchesGlobPatterns:

    def test_simple_match(self, cat):
        assert cat._matches_glob_patterns("Screenshot_001.png", ["Screenshot_*"]) is True

    def test_wildcard_middle(self, cat):
        assert cat._matches_glob_patterns("IMG-20240315-WA0001.jpg", ["IMG-*-WA*"]) is True

    def test_no_match(self, cat):
        assert cat._matches_glob_patterns("photo.jpg", ["Screenshot_*"]) is False

    def test_empty_patterns(self, cat):
        assert cat._matches_glob_patterns("anything.jpg", []) is False

    def test_multiple_patterns_any_match(self, cat):
        assert cat._matches_glob_patterns(
            "FB_IMG_123.jpg", ["IMG-*-WA*", "FB_IMG_*", "received_*"],
        ) is True


class TestMatchesScreenshotResolution:

    def test_exact_match(self, cat):
        assert cat._matches_screenshot_resolution(1080, 1920) is True

    def test_within_tolerance(self, cat):
        assert cat._matches_screenshot_resolution(1085, 1925) is True

    def test_outside_tolerance(self, cat):
        assert cat._matches_screenshot_resolution(1100, 1920) is False

    def test_rotated(self, cat):
        assert cat._matches_screenshot_resolution(1920, 1080) is True

    def test_none_dimensions(self, cat):
        assert cat._matches_screenshot_resolution(None, 1920) is False
        assert cat._matches_screenshot_resolution(1080, None) is False


class TestMatchesDisplayHeuristic:

    def test_16_9(self, cat):
        assert cat._matches_display_heuristic(1920, 1080, False, False) is True

    def test_9_16(self, cat):
        assert cat._matches_display_heuristic(1080, 1920, False, False) is True

    def test_4_3(self, cat):
        assert cat._matches_display_heuristic(1024, 768, False, False) is True

    def test_3_4(self, cat):
        assert cat._matches_display_heuristic(768, 1024, False, False) is True

    def test_9_19_5(self, cat):
        """1080 × 2340 = 60 × (18:39)."""
        assert cat._matches_display_heuristic(1080, 2340, False, False) is True

    def test_19_5_9_rotated(self, cat):
        assert cat._matches_display_heuristic(2340, 1080, False, False) is True

    def test_non_standard_ratio(self, cat):
        assert cat._matches_display_heuristic(1000, 1001, False, False) is False

    def test_blocked_by_camera(self, cat):
        assert cat._matches_display_heuristic(1920, 1080, True, False) is False

    def test_blocked_by_gps(self, cat):
        assert cat._matches_display_heuristic(1920, 1080, False, True) is False

    def test_none_dimensions(self, cat):
        assert cat._matches_display_heuristic(None, 1080, False, False) is False

    def test_zero_dimensions(self, cat):
        assert cat._matches_display_heuristic(0, 0, False, False) is False

    def test_2560_1440_16_9(self, cat):
        """2560 × 1440 = 160 × (16:9)."""
        assert cat._matches_display_heuristic(2560, 1440, False, False) is True


# ===========================================================================
# 7.  Module-level constants sanity
# ===========================================================================

class TestConstants:

    def test_raw_formats_all_lowercase(self):
        for fmt in _RAW_FORMATS:
            assert fmt == fmt.lower(), f"{fmt} should be lowercase"

    def test_display_ratios_non_empty(self):
        assert len(_DISPLAY_RATIOS) >= 3
