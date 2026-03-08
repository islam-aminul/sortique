# Configuration Reference

Config is loaded from `~/.sortique/config.json` (created on first run with defaults). The settings UI writes to this file; you can also edit it directly.

**Priority order** (highest wins):
1. Runtime overrides set via the API
2. `~/.sortique/config.json`
3. Built-in defaults (`config/defaults.json` in the package)

---

## Options

### Processing

| Key | Type | Default | Description |
|---|---|---|---|
| `threads` | int | `4` | Number of parallel worker threads. Range: 1–16. |
| `verify_copies` | bool | `false` | After copying each file, verify the destination matches the source via SHA-256. Slower but ensures data integrity. |
| `follow_symlinks` | bool | `false` | Follow symbolic links when scanning source directories. When `false`, symlinks are counted and skipped. |

### Image Export

| Key | Type | Default | Description |
|---|---|---|---|
| `jpeg_quality` | int | `90` | JPEG compression quality for resized exports. Range: 1–100. |
| `max_resolution` | [int, int] | `[3840, 2160]` | Maximum output resolution `[width, height]` for image exports. Originals and RAW files are downscaled to fit this box; aspect ratio is preserved. Default is 4K (UHD). |

### Screenshot Detection

| Key | Type | Default | Description |
|---|---|---|---|
| `screenshot_resolutions` | list[list[int]] | _(16 phone resolutions)_ | List of `[width, height]` pairs considered screenshot dimensions. A file is classified as a screenshot if its pixel dimensions match any entry within `screenshot_tolerance`. |
| `screenshot_tolerance` | int | `10` | Pixel tolerance when comparing image dimensions against `screenshot_resolutions`. A difference of ≤ 10 px in each axis still counts as a match. |

### Filename Pattern Detection

| Key | Type | Default | Description |
|---|---|---|---|
| `social_media_image_patterns` | list[str] | `["IMG-*-WA*", "FB_IMG_*", "received_*"]` | Glob patterns for social media image filenames (WhatsApp, Facebook). Matched files are placed in `Social Media/`. |
| `social_media_video_patterns` | list[str] | `["VID-*-WA*", "FB_VID_*"]` | Glob patterns for social media video filenames. |
| `motion_photo_patterns` | list[str] | `["*_MVIMG_*", "MOTION_*"]` | Glob patterns identifying Motion Photos (Google, Samsung). |
| `screenshot_filename_patterns` | list[str] | `["Screenshot_*", "SCR_*"]` | Glob patterns identifying screenshots by filename (in addition to resolution-based detection). |
| `voice_note_patterns` | list[str] | `["Recording_*", "Voice_*", "Audio_*"]` | Glob patterns identifying voice notes. |
| `burst_filename_patterns` | list[str] | `["*_BURST*", "*_BRACKETED*"]` | Glob patterns for burst or bracketed capture sequences. |

### Editor / EXIF Detection

| Key | Type | Default | Description |
|---|---|---|---|
| `editor_patterns` | list[str] | `["Adobe Photoshop", "Adobe Lightroom", "GIMP", "Snapseed", ...]` | Substrings matched against the EXIF `Software` tag. A match classifies the image as `Edited`. Case-insensitive. |
| `editor_exclusions` | list[str] | `[]` | Substrings that suppress editor detection even if `editor_patterns` matched. Useful for excluding camera firmware strings that contain "Adobe" etc. |

### Date Extraction

| Key | Type | Default | Description |
|---|---|---|---|
| `date_regex_patterns` | list[str] | _(4 patterns)_ | Regular expressions applied to filenames to extract dates. Capture groups must yield year, month, day (and optionally hour, minute, second). Built-in patterns cover `YYYY-MM-DD HH:MM:SS`, `YYYYMMDDHHMMSS`, `YYYY-MM-DD`, and `DD-MM-YYYY`. |

### Audio

| Key | Type | Default | Description |
|---|---|---|---|
| `musicbrainz_enabled` | bool | `false` | Enable MusicBrainz metadata lookup for audio files. Adds artist, album, and release year when tags are missing. Requires network access. |

### Sidecars

| Key | Type | Default | Description |
|---|---|---|---|
| `sidecar_extensions` | list[str] | `[".thm", ".srt", ".sub", ".lrc", ".xmp", ".aae"]` | File extensions treated as sidecar files (thumbnails, subtitles, lyrics, XMP metadata). Sidecars are moved alongside their primary file. |

---

## Example `config.json`

```json
{
  "threads": 8,
  "jpeg_quality": 90,
  "max_resolution": [3840, 2160],
  "verify_copies": true,
  "follow_symlinks": false,
  "musicbrainz_enabled": false,
  "screenshot_tolerance": 10,
  "screenshot_resolutions": [
    [750, 1334],
    [1080, 1920],
    [1125, 2436],
    [1170, 2532],
    [1179, 2556],
    [1242, 2688],
    [1284, 2778],
    [1290, 2796],
    [1440, 2560],
    [1440, 3200],
    [1536, 2048],
    [2048, 2732],
    [1080, 2400],
    [1440, 3120],
    [2160, 3840],
    [828, 1792]
  ],
  "editor_patterns": [
    "Adobe Photoshop",
    "Adobe Lightroom",
    "GIMP",
    "Snapseed",
    "Google Photos",
    "Picasa",
    "Instagram",
    "VSCO",
    "Afterlight",
    "PicsArt"
  ],
  "editor_exclusions": [],
  "social_media_image_patterns": ["IMG-*-WA*", "FB_IMG_*", "received_*"],
  "social_media_video_patterns": ["VID-*-WA*", "FB_VID_*"],
  "motion_photo_patterns": ["*_MVIMG_*", "MOTION_*"],
  "screenshot_filename_patterns": ["Screenshot_*", "SCR_*"],
  "voice_note_patterns": ["Recording_*", "Voice_*", "Audio_*"],
  "burst_filename_patterns": ["*_BURST*", "*_BRACKETED*"],
  "sidecar_extensions": [".thm", ".srt", ".sub", ".lrc", ".xmp", ".aae"],
  "date_regex_patterns": [
    "(\\d{4})-(\\d{2})-(\\d{2})[ _T](\\d{2})[-:](\\d{2})[-:](\\d{2})",
    "(\\d{4})(\\d{2})(\\d{2})(\\d{2})(\\d{2})(\\d{2})",
    "(\\d{4})-(\\d{2})-(\\d{2})",
    "(\\d{2})-(\\d{2})-(\\d{4})"
  ]
}
```
