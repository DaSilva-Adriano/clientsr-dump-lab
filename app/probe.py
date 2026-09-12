"""ffprobe + height class + tool-path status."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.settings import AppConfig, resolve_animejanai_binary
from app.winproc import run_capture

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".avi",
    ".mov",
    ".m4v",
    ".ts",
    ".m2ts",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".flv",
}

CLASS_360_TO_720 = "360p→720p"
CLASS_1080_TO_4K = "1080p→4K"
CLASS_UNSUPPORTED = "unsupported"
UNSUPPORTED_REASON = "unsupported source height (2× only: 360p or 1080p)"

# Nominal 2× canvases used by the force-override dropdown (16:9 thesis sizes).
FORCE_720P = (1280, 720)
FORCE_2160P = (3840, 2160)
UHD_4K = FORCE_2160P  # 3840×2160 — optional post-2× bicubic target

STANDARD_FPS = (24.0, 25.0, 30.0, 50.0, 60.0)
STANDARD_FPS_NEAR = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)
# Same tolerance the thesis eval host uses for ref vs dist alignment.
FPS_MATCH_TOLERANCE = 0.05


@dataclass
class ProbeResult:
    path: Path
    width: int = 0
    height: int = 0
    fps: float = 0.0
    fps_str: str = ""
    duration: float = 0.0
    has_audio: bool = False
    video_codec: str = ""
    audio_codec: str | None = None
    error: str | None = None


@dataclass
class HeightPlan:
    height_class: str
    target_w: int | None
    target_h: int | None
    reason: str = ""
    forced: bool = False
    fps_warning: bool = False


@dataclass
class ToolStatus:
    name: str
    path: str
    found: bool
    detail: str = ""


def parse_rate(raw: str | None) -> tuple[float, str]:
    if not raw or raw in {"0/0", "N/A", "nan"}:
        return 0.0, ""
    s = str(raw).strip()
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            num, den = int(a), int(b)
            if den == 0:
                return 0.0, s
            return num / den, s
        val = float(s)
        return val, s
    except (TypeError, ValueError):
        return 0.0, s


def fps_is_standard(fps: float) -> bool:
    if fps <= 0:
        return False
    for ref in STANDARD_FPS_NEAR:
        if abs(fps - ref) < 0.08:
            return True
    return False


def fps_matches(
    actual: float,
    expected: float,
    *,
    tol: float = FPS_MATCH_TOLERANCE,
) -> bool:
    """True when output fps matches the source (or there is no source rate to check)."""
    if expected <= 0:
        return True
    if actual <= 0:
        return False
    return abs(actual - expected) <= tol


def classify_height(
    width: int,
    height: int,
    force_target: str | None = None,
) -> HeightPlan:
    """2× only: 360p-class (h 300–400) → 2× (~720p); 1080p-class (h 1000–1200) → 2× (~4K).

    Force override (`720p` | `2160p`) is for odd sizes. Native model factor is
    always 2×; we lock the mpv output canvas to the chosen target.
    """
    force = (force_target or "").strip().lower()
    if force in {"720p", "720"}:
        tw, th = FORCE_720P
        return HeightPlan(CLASS_360_TO_720, tw, th, forced=True)
    if force in {"2160p", "4k", "2160"}:
        tw, th = FORCE_2160P
        return HeightPlan(CLASS_1080_TO_4K, tw, th, forced=True)

    if 300 <= height <= 400:
        return HeightPlan(CLASS_360_TO_720, width * 2, height * 2)
    if 1000 <= height <= 1200:
        return HeightPlan(CLASS_1080_TO_4K, width * 2, height * 2)
    return HeightPlan(
        CLASS_UNSUPPORTED,
        None,
        None,
        reason=UNSUPPORTED_REASON,
    )


def is_uhd_4k(width: int | None, height: int | None) -> bool:
    return width == UHD_4K[0] and height == UHD_4K[1]


def needs_bicubic_to_4k(width: int | None, height: int | None) -> bool:
    """True when a 2× canvas should be FFmpeg-bicubic-scaled to 3840×2160.

    Only enlarges. Already-4K canvases are left alone. Anything larger than
    UHD in either dimension is not downscaled.
    """
    if width is None or height is None:
        return False
    if width <= 0 or height <= 0:
        return False
    if is_uhd_4k(width, height):
        return False
    return width <= UHD_4K[0] and height <= UHD_4K[1]


def expected_output_size(
    target_w: int | None,
    target_h: int | None,
    *,
    bicubic_to_4k: bool,
) -> tuple[int, int]:
    """Delivered MP4 canvas: native 2×, or UHD if bicubic-to-4K applies."""
    tw = int(target_w or 0)
    th = int(target_h or 0)
    if bicubic_to_4k and needs_bicubic_to_4k(tw, th):
        return UHD_4K
    return tw, th


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_fps(fps: float, fps_str: str = "") -> str:
    if fps_str and "/" in fps_str:
        val, _ = parse_rate(fps_str)
        if val > 0:
            fps = val
    if fps <= 0:
        return "—"
    if abs(fps - round(fps)) < 0.011:
        return str(int(round(fps)))
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def probe_file(ffprobe: Path, source: Path) -> ProbeResult:
    if not ffprobe.is_file():
        return ProbeResult(
            path=source,
            error=f"ffprobe missing: {ffprobe}",
        )
    if not source.is_file():
        return ProbeResult(path=source, error=f"file not found: {source}")

    argv = [
        str(ffprobe),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]
    try:
        proc = run_capture(argv, timeout=60)
    except Exception as exc:
        return ProbeResult(path=source, error=f"ffprobe failed: {exc}")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        return ProbeResult(path=source, error=f"ffprobe: {err}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return ProbeResult(path=source, error=f"ffprobe JSON: {exc}")

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return ProbeResult(path=source, error="no video stream")

    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (TypeError, ValueError):
        width, height = 0, 0
    fps, fps_str = parse_rate(video.get("r_frame_rate"))
    if fps <= 0:
        fps, fps_str = parse_rate(video.get("avg_frame_rate"))

    duration = 0.0
    fmt = data.get("format") or {}
    for candidate in (
        video.get("duration"),
        fmt.get("duration"),
    ):
        try:
            if candidate is not None:
                duration = float(candidate)
                if duration > 0:
                    break
        except (TypeError, ValueError):
            continue

    return ProbeResult(
        path=source,
        width=width,
        height=height,
        fps=fps,
        fps_str=fps_str,
        duration=duration,
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=(str(audio.get("codec_name")) if audio else None),
    )


def probe_output_height(ffprobe: Path, dest: Path) -> tuple[int, int]:
    result = probe_file(ffprobe, dest)
    if result.error:
        raise RuntimeError(result.error)
    return result.width, result.height


def collect_videos(paths: list[Path], *, recursive: bool = True) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            if p.suffix.lower() in VIDEO_EXTENSIONS:
                key = str(p.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    files.append(p)
            continue
        if p.is_dir():
            iterator = p.rglob("*") if recursive else p.glob("*")
            for child in iterator:
                if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS:
                    try:
                        key = str(child.resolve()).lower()
                    except OSError:
                        key = str(child).lower()
                    if key not in seen:
                        seen.add(key)
                        files.append(child)
    files.sort(key=lambda x: str(x).lower())
    return files


def gpu_name() -> str | None:
    try:
        proc = run_capture(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            timeout=8,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip().splitlines()
    return line[0].strip() if line else None


def ffmpeg_has_libx265(ffmpeg: Path) -> bool:
    if not ffmpeg.is_file():
        return False
    try:
        proc = run_capture([str(ffmpeg), "-hide_banner", "-encoders"], timeout=20)
    except Exception:
        return False
    return "libx265" in (proc.stdout or "")


def probe_tools(cfg: AppConfig) -> list[ToolStatus]:
    rows: list[ToolStatus] = []

    def file_row(name: str, path: Path) -> ToolStatus:
        found = path.is_file()
        return ToolStatus(name, str(path), found, "Found" if found else "Missing")

    def dir_row(name: str, path: Path) -> ToolStatus:
        found = path.is_dir()
        return ToolStatus(name, str(path), found, "Found" if found else "Missing")

    rows.append(file_row("ffmpeg", cfg.ffmpeg_path()))
    rows.append(file_row("ffprobe", cfg.ffprobe_path()))
    rows.append(file_row("mpv", cfg.mpv_path()))
    rows.append(dir_row("mpv portable_config", cfg.mpv_portable_path()))

    ani = resolve_animejanai_binary(cfg.animejanai_path())
    if ani is not None:
        rows.append(ToolStatus("AnimeJaNai", str(ani), True, "Found"))
    else:
        rows.append(
            ToolStatus(
                "AnimeJaNai",
                str(cfg.animejanai_path()),
                False,
                "Missing (tried mpv.exe, mpvnet.exe)",
            )
        )

    rows.append(dir_row("shaders", cfg.shaders_path()))
    return rows
