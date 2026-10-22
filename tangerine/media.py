"""External tool discovery and media probing (ffmpeg / ffprobe / WinRAR)."""

import json
import os
import re
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import paths

# Zero off Windows: subprocess rejects any non-zero creationflags on POSIX.
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_POPEN_KW = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}


def _candidates(name: str) -> Iterable[Path]:
    exe = f"{name}.exe" if os.name == "nt" else name
    yield paths.tools_dir() / exe
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget.exists():
        try:
            for pkg in winget.iterdir():
                if "FFmpeg" in pkg.name or "ffmpeg" in pkg.name:
                    for found in pkg.rglob(exe):
                        if found.parent.name.lower() == "bin":
                            yield found
        except OSError:
            pass
    for base in (
        Path("C:/ffmpeg/bin"),
        Path("C:/Program Files/ffmpeg/bin"),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ffmpeg" / "bin",
    ):
        yield base / exe
    which = shutil.which(name)
    if which:
        yield Path(which)


_cache: dict[str, str] = {}


def _locate(name: str) -> str | None:
    if name in _cache:
        return _cache[name]
    for candidate in _candidates(name):
        if candidate.is_file():
            _cache[name] = str(candidate)
            return str(candidate)
    return None


def ffmpeg_path() -> str | None:
    return _locate("ffmpeg")


def ffprobe_path() -> str | None:
    return _locate("ffprobe")


def _winrar_dir() -> Path:
    for base in (Path("C:/Program Files/WinRAR"), Path("C:/Program Files (x86)/WinRAR")):
        if base.exists():
            return base
    return Path("")


def rar_path() -> str | None:
    if os.name != "nt":
        return shutil.which("rar")
    exe = _winrar_dir() / "Rar.exe"
    return str(exe) if exe.is_file() else None


def unrar_path() -> str | None:
    if os.name != "nt":
        # RARLAB's unrar, or rar itself, share WinRAR's command syntax.
        return shutil.which("unrar") or shutil.which("rar")
    exe = _winrar_dir() / "UnRAR.exe"
    return str(exe) if exe.is_file() else None


def sevenzip_path() -> str | None:
    """p7zip/7-Zip can extract RAR and RAR5 where unrar is not installed."""
    if os.name == "nt":
        return None
    return shutil.which("7z") or shutil.which("7zz")


def has_rar_engine() -> bool:
    return unrar_path() is not None or sevenzip_path() is not None


def run_process(
    args: list[str],
    cwd: Path | None = None,
    on_line: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> int:
    """Run a console tool, streaming stdout lines. Returns exit code (-9 on cancel)."""
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
            **_POPEN_KW,
        )
    except OSError:
        return 127
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                return -9
            if on_line:
                on_line(line.rstrip("\n"))
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
    return proc.wait()


@dataclass
class StreamInfo:
    kind: str = ""
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    channels: int = 0
    sample_rate: int = 0
    bit_rate: int = 0
    pix_fmt: str = ""


@dataclass
class MediaInfo:
    duration: float = 0.0
    bit_rate: int = 0
    format_name: str = ""
    streams: list[StreamInfo] = field(default_factory=list)

    def video(self) -> StreamInfo | None:
        for s in self.streams:
            if s.kind == "video":
                return s
        return None

    def audio(self) -> StreamInfo | None:
        for s in self.streams:
            if s.kind == "audio":
                return s
        return None

    def video_bit_rate(self) -> int:
        video = self.video()
        if video and video.bit_rate:
            return video.bit_rate
        total = self.bit_rate
        audio = self.audio()
        if total and audio:
            return max(total - (audio.bit_rate or 128_000), 60_000)
        return total
    
    def has_hdr(self) -> bool:
        video = self.video()
        if not video:
            return False
        return video.pix_fmt in ("yuv420p10le", "yuv422p10le", "yuv444p10le", "p010le", "p016le")


def probe(path: Path) -> MediaInfo:
    probe_exe = ffprobe_path()
    info = MediaInfo()
    if not probe_exe:
        return info
    args = [
        probe_exe,
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        str(path),
    ]
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, **_POPEN_KW,
        ).stdout
        data = json.loads(out or "{}")
    except Exception:
        return info
    fmt = data.get("format") or {}
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0
    try:
        info.bit_rate = int(fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        info.bit_rate = 0
    info.format_name = fmt.get("format_name") or ""
    for raw in data.get("streams") or []:
        stream = StreamInfo()
        stream.kind = raw.get("codec_type") or ""
        stream.codec = raw.get("codec_name") or ""
        try:
            stream.width = int(raw.get("width") or 0)
            stream.height = int(raw.get("height") or 0)
            stream.channels = int(raw.get("channels") or 0)
            stream.sample_rate = int(raw.get("sample_rate") or 0)
        except (TypeError, ValueError):
            pass
        stream.pix_fmt = raw.get("pix_fmt") or ""
        try:
            stream.bit_rate = int(raw.get("bit_rate") or 0)
        except (TypeError, ValueError):
            stream.bit_rate = 0
        rate = raw.get("avg_frame_rate") or raw.get("r_frame_rate") or "0/0"
        try:
            num, _, den = rate.partition("/")
            den_f = float(den) if den else 1.0
            stream.fps = float(num) / den_f if den_f else 0.0
        except (TypeError, ValueError):
            stream.fps = 0.0
        info.streams.append(stream)
    return info


_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_PROGRESS_LINE_RE = re.compile(
    r"^(frame|fps|stream_\w+|bitrate|total_size|out_time\w*|dup_frames|drop_frames|speed|progress|lsize|time)="
)


def probe_duration_seconds(path: Path) -> float:
    return probe(path).duration


def run_ffmpeg(
    args: list[str],
    duration: float,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    tail: list[str] | None = None,
) -> int:
    """Run ffmpeg with -progress pipe:1 and report 0.0-1.0 completion."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return 127
    full = [ffmpeg, "-hide_banner", "-nostdin", "-y"] + args + ["-progress", "pipe:1", "-nostats"]
    last = [0.0]
    recent: deque[str] | None = deque(maxlen=15) if tail is not None else None

    def handle(line: str) -> None:
        stripped = line.strip()
        if recent is not None and stripped and not _PROGRESS_LINE_RE.match(stripped):
            recent.append(stripped)
        if not on_progress or duration <= 0:
            return
        match = _TIME_RE.search(line)
        if not match:
            return
        hours, minutes, seconds = match.groups()
        total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        fraction = max(0.0, min(total / duration, 0.995))
        if fraction > last[0]:
            last[0] = fraction
            on_progress(fraction)

    code = run_process(full, on_line=handle, cancel=cancel)
    if tail is not None:
        tail[:] = recent
    if code == 0 and on_progress:
        on_progress(1.0)
    return code
