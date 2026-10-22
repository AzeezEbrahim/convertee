"""Ola B format matrix (v1.7.0): AVIF, OGG/Opus/AIFF/WMA, WebM/AVI/WMV and the
SRT/VTT subtitle family, verified end to end with Pillow and ffprobe.

Sources are built once inside ``%TEMP%\\tangerine_fixtures\\v17`` and reused on
later runs; every FFmpeg-dependent test skips cleanly when it is missing, so
the suite stays green on machines without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from tangerine import catalog, engines, media, tools

FIXTURES = Path(tempfile.gettempdir()) / "tangerine_fixtures" / "v17"

requires_ffmpeg = pytest.mark.skipif(
    not media.ffmpeg_path() or not media.ffprobe_path(),
    reason="FFmpeg and ffprobe are required for the media format matrix",
)

requires_heif = pytest.mark.skipif(
    "heic" not in catalog.available_engines(),
    reason="pillow-heif is required for HEIF/AVIF support",
)

#: Deliberately CP1252 + CRLF: subtitle reading must be tolerant.
SRT_SOURCE = (
    "1\r\n"
    "00:00:01,000 --> 00:00:02,500\r\n"
    "Hola café\r\n"
    "\r\n"
    "2\r\n"
    "00:00:03,000 --> 00:00:04,750\r\n"
    "Second line\r\n"
)

VTT_SOURCE = (
    "WEBVTT\n"
    "\n"
    "00:00:01.000 --> 00:00:02.500\n"
    "Hola café\n"
    "\n"
    "00:00:03.000 --> 00:00:04.750\n"
    "Second line\n"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(
        [media.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
    )


def _probe(path: Path) -> tuple[dict, list[dict]]:
    output = subprocess.run(
        [media.ffprobe_path(), "-v", "error", "-show_format", "-show_streams",
         "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    data = json.loads(output)
    return data["format"], data["streams"]


def _stream(path: Path, kind: str) -> dict:
    return next(stream for stream in _probe(path)[1] if stream["codec_type"] == kind)


def _duration(path: Path) -> float:
    return float(_probe(path)[0]["duration"])


def _source(fixtures: Path, tmp_path: Path, name: str) -> Path:
    """Copy a shared fixture into the test folder so outputs stay local."""
    target = tmp_path / name
    shutil.copy2(fixtures / name, target)
    return target


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fixtures() -> Path:
    """Materialise the v1.7.0 sources once and return their folder."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    photo = FIXTURES / "photo.jpg"
    if not photo.exists():
        Image.new("RGB", (96, 64), (248, 120, 0)).save(photo, "JPEG", quality=92)
    anim = FIXTURES / "anim.gif"
    if not anim.exists():
        Image.new("RGB", (32, 32), (255, 128, 0)).save(anim, "GIF")
    srt = FIXTURES / "sample.srt"
    if not srt.exists():
        srt.write_bytes(SRT_SOURCE.encode("cp1252"))
    vtt = FIXTURES / "sample.vtt"
    if not vtt.exists():
        vtt.write_text(VTT_SOURCE, encoding="utf-8")
    if media.ffmpeg_path():
        tone = FIXTURES / "tone.wav"
        if not tone.exists():
            _run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                         "-c:a", "pcm_s16le", str(tone)])
        clip = FIXTURES / "clip.mp4"
        if not clip.exists():
            _run_ffmpeg([
                "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(clip),
            ])
        odd = FIXTURES / "clip_odd.avi"
        if not odd.exists():
            # Older ffmpeg (4.4 on Ubuntu 22.04) rounds lavfi colour sources
            # to even sizes; a still image keeps the odd 321x241 frame.
            frame = FIXTURES / "clip_odd_frame.png"
            Image.new("RGB", (321, 241), "orange").save(frame)
            _run_ffmpeg([
                "-loop", "1", "-i", str(frame), "-r", "5", "-t", "1",
                "-c:v", "rawvideo", "-pix_fmt", "bgr24", str(odd),
            ])
    return FIXTURES


# ---------------------------------------------------------------------------
# catalog / icons
# ---------------------------------------------------------------------------

def test_new_extensions_are_catalogued():
    assert ".avif" in catalog.IMAGE_EXTS and ".avif" in catalog.RASTER_EXTS
    assert catalog.IMAGE_TARGETS == ["jpg", "png", "webp", "avif", "heic", "tiff", "pdf"]
    assert {".ogg", ".opus", ".aiff", ".aif", ".wma"} <= catalog.AUDIO_EXTS
    assert {"ogg", "opus", "aiff", "wma"} <= set(catalog.AUDIO_TARGETS)
    assert ".wmv" in catalog.VIDEO_EXTS
    assert catalog.VIDEO_TARGETS == [
        "mp4", "mov", "mkv", "webm", "avi", "wmv", "gif", "mp3", "m4a"]
    assert {"webm", "avi", "wmv"} <= set(catalog.GIF_TARGETS)


def test_subtitle_family_has_no_tools(fixtures):
    assert catalog.family_of(fixtures / "sample.srt") == catalog.FAMILY_SUB
    assert catalog.family_of(fixtures / "sample.vtt") == catalog.FAMILY_SUB
    assert catalog.SUB_TARGETS[".srt"] == ("vtt", "txt")
    assert catalog.SUB_TARGETS[".vtt"] == ("srt", "txt")
    assert catalog.tools_for([fixtures / "sample.srt"]) == []


def test_subtitle_conversions_offered(fixtures):
    def offered(paths: list[Path]) -> set[str]:
        return {c.target_ext for c in catalog.conversions_for(paths)}

    assert offered([fixtures / "sample.srt"]) == {"vtt", "txt"}
    assert offered([fixtures / "sample.vtt"]) == {"srt", "txt"}
    # Formats already present in the selection are not offered again.
    mixed = [fixtures / "sample.srt", fixtures / "sample.vtt"]
    assert offered(mixed) == {"txt"}


@requires_heif
def test_image_conversions_offer_avif(fixtures):
    offered = {c.target_ext for c in catalog.conversions_for([fixtures / "photo.jpg"])}
    assert {"avif", "png", "webp", "heic", "tiff", "pdf"} <= offered
    assert "jpg" not in offered


def test_audio_codec_args_cover_new_containers():
    assert tools.audio_codec_args(".ogg") == ["-c:a", "libvorbis", "-q:a", "5"]
    assert tools.audio_codec_args("tone.opus") == ["-c:a", "libopus", "-b:a", "128k"]
    assert tools.audio_codec_args("tone.aiff") == ["-c:a", "pcm_s16be"]
    assert tools.audio_codec_args("tone.aif") == ["-c:a", "pcm_s16be"]
    assert tools.audio_codec_args("tone.wma") == ["-c:a", "wmav2", "-b:a", "192k"]


def test_icons_cover_new_extensions():
    from tangerine import icons

    assert icons.icon_for(".srt") == "text"
    assert icons.icon_for("vtt") == "text"
    assert icons.icon_for(".aif") == "music"
    for ext in ("avif", "ogg", "opus", "aiff", "wma", "webm", "avi", "wmv"):
        assert icons.has_icon(ext), ext


# ---------------------------------------------------------------------------
# AVIF
# ---------------------------------------------------------------------------

@requires_heif
def test_avif_write_read_and_back(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "photo.jpg")
    with Image.open(source) as original:
        expected = original.size

    avif = engines.convert_single(source, "avif", engines.Ctx(), set())[0]
    assert avif.name == "photo.avif"
    assert avif.stat().st_size > 0
    with Image.open(avif) as reopened:
        reopened.load()
        assert reopened.format == "AVIF"
        assert reopened.size == expected

    for target in ("jpg", "png"):
        back = engines.convert_single(avif, target, engines.Ctx(), set())[0]
        assert back.suffix == f".{target}"
        with Image.open(back) as reopened:
            reopened.load()
            assert reopened.size == expected


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------

@requires_ffmpeg
def test_audio_targets_matrix(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "tone.wav")
    expected = {"ogg": "vorbis", "opus": "opus", "aiff": "pcm_s16be", "wma": "wmav2"}
    for target, codec in expected.items():
        output = engines.convert_single(source, target, engines.Ctx(), set())[0]
        assert output.name == f"tone.{target}"
        assert output.stat().st_size > 0
        assert _stream(output, "audio")["codec_name"] == codec
        assert _duration(output) == pytest.approx(3.0, abs=0.2)


@requires_ffmpeg
def test_audio_new_formats_round_trip_through_mp3(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "tone.wav")
    for target in ("ogg", "opus", "aiff", "wma"):
        converted = engines.convert_single(source, target, engines.Ctx(), set())[0]
        back = engines.convert_single(converted, "mp3", engines.Ctx(), set())[0]
        assert back.suffix == ".mp3"
        assert _stream(back, "audio")["codec_name"] == "mp3"
        assert _duration(back) == pytest.approx(3.0, abs=0.2)


@requires_ffmpeg
def test_audio_tools_accept_new_containers(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "tone.wav")
    ogg = engines.convert_single(source, "ogg", engines.Ctx(), set())[0]
    opus = engines.convert_single(source, "opus", engines.Ctx(), set())[0]
    aiff = engines.convert_single(source, "aiff", engines.Ctx(), set())[0]
    wma = engines.convert_single(source, "wma", engines.Ctx(), set())[0]

    trimmed = tools.trim_audio(ogg, 0.0, 1.0, engines.Ctx(), set())
    assert _stream(trimmed, "audio")["codec_name"] == "vorbis"
    assert _duration(trimmed) == pytest.approx(1.0, abs=0.25)

    mono = tools.convert_channels(opus, "mono", engines.Ctx(), set())
    assert _stream(mono, "audio")["codec_name"] == "opus"
    assert _stream(mono, "audio")["channels"] == 1

    normalized = tools.normalize_audio(aiff, engines.Ctx(), set())
    assert _stream(normalized, "audio")["codec_name"] == "pcm_s16be"

    bleeped = tools.bleep_audio(wma, [(0.5, 1.0)], engines.Ctx(), set())
    assert _stream(bleeped, "audio")["codec_name"] == "wmav2"

    compressed = tools.compress_audio(ogg, "balanced", engines.Ctx(), set())
    assert _stream(compressed, "audio")["codec_name"] == "vorbis"


# ---------------------------------------------------------------------------
# video
# ---------------------------------------------------------------------------

@requires_ffmpeg
def test_video_targets_matrix(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "clip.mp4")
    expected = {
        "webm": ("webm", "vp9", "opus"),
        "avi": ("avi", "mpeg4", "mp3"),
        "wmv": ("asf", "wmv2", "wmav2"),
    }
    for target, (format_name, video_codec, audio_codec) in expected.items():
        output = engines.convert_single(source, target, engines.Ctx(), set())[0]
        assert output.name == f"clip.{target}"
        assert output.stat().st_size > 0
        assert format_name in _probe(output)[0]["format_name"], target
        video = _stream(output, "video")
        assert video["codec_name"] == video_codec, target
        assert video["width"] % 2 == 0 and video["height"] % 2 == 0, target
        assert _stream(output, "audio")["codec_name"] == audio_codec, target
        assert _duration(output) == pytest.approx(3.0, abs=0.25), target


@requires_ffmpeg
def test_video_forces_even_dimensions_from_odd_source(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "clip_odd.avi")
    video = _stream(source, "video")
    assert (video["width"], video["height"]) == (321, 241)
    for target in ("webm", "avi", "wmv"):
        output = engines.convert_single(source, target, engines.Ctx(), set())[0]
        video = _stream(output, "video")
        assert (video["width"], video["height"]) == (320, 240), target


@requires_ffmpeg
def test_gif_converts_to_new_video_targets(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "anim.gif")
    expected = {"webm": ("webm", "vp9"), "avi": ("avi", "mpeg4"), "wmv": ("asf", "wmv2")}
    for target, (format_name, codec) in expected.items():
        output = engines.convert_single(source, target, engines.Ctx(), set())[0]
        assert output.stat().st_size > 0
        assert format_name in _probe(output)[0]["format_name"], target
        assert _stream(output, "video")["codec_name"] == codec, target


@requires_ffmpeg
def test_wmv_source_converts_to_mp4(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "clip.mp4")
    wmv = engines.convert_single(source, "wmv", engines.Ctx(), set())[0]
    mp4 = engines.convert_single(wmv, "mp4", engines.Ctx(), set())[0]
    assert mp4.suffix == ".mp4"
    assert _stream(mp4, "video")["codec_name"] == "h264"
    assert _stream(mp4, "audio")["codec_name"] == "aac"
    assert _duration(mp4) == pytest.approx(3.0, abs=0.25)


# ---------------------------------------------------------------------------
# subtitles (no external tools)
# ---------------------------------------------------------------------------

def test_srt_to_vtt_normalises_timestamps_and_encoding(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "sample.srt")
    assert b"caf\xe9" in source.read_bytes()  # CP1252 source on purpose

    output = engines.convert_single(source, "vtt", engines.Ctx(), set())[0]
    assert output.name == "sample.vtt"
    text = output.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    assert [line for line in text.splitlines() if "-->" in line] == [
        "00:00:01.000 --> 00:00:02.500",
        "00:00:03.000 --> 00:00:04.750",
    ]
    assert "Hola café" in text
    assert "\r" not in text


def test_vtt_to_srt_uses_numbers_arrows_and_commas(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "sample.vtt")
    output = engines.convert_single(source, "srt", engines.Ctx(), set())[0]
    assert output.name == "sample.srt"
    text = output.read_text(encoding="utf-8")
    assert "WEBVTT" not in text
    lines = text.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:01,000 --> 00:00:02,500"
    assert lines[4] == "2"
    assert lines[5] == "00:00:03,000 --> 00:00:04,750"
    assert "Hola café" in text
    assert "\r" not in text


def test_subtitle_to_txt_is_plain_text(fixtures, tmp_path):
    source = _source(fixtures, tmp_path, "sample.srt")
    assert b"\r\n" in source.read_bytes()
    output = engines.convert_single(source, "txt", engines.Ctx(), set())[0]
    text = output.read_text(encoding="utf-8")
    assert "-->" not in text
    assert text == "Hola café\nSecond line\n"

    from_vtt = engines.convert_single(
        _source(fixtures, tmp_path, "sample.vtt"), "txt", engines.Ctx(), set())[0]
    assert from_vtt.read_text(encoding="utf-8") == text
