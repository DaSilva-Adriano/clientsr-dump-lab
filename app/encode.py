"""Thesis FFmpeg conform: libx265 CRF 12 MP4, audio copy, no frame interpolation."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from app.winproc import format_cmd, run_logged

ProgressCb = Callable[[str, float | None], None]
LogCb = Callable[[str], None]


class EncodeError(RuntimeError):
    pass


def parse_ffmpeg_progress(line: str) -> dict[str, str]:
    if "=" not in line:
        return {}
    key, _, val = line.partition("=")
    return {key.strip(): val.strip()}


def _out_time_seconds(fields: dict[str, str]) -> float | None:
    # FFmpeg names out_time_ms but the unit is microseconds; prefer out_time_us.
    if "out_time_us" in fields:
        try:
            return int(fields["out_time_us"]) / 1_000_000.0
        except ValueError:
            pass
    if "out_time_ms" in fields:
        try:
            return int(fields["out_time_ms"]) / 1_000_000.0
        except ValueError:
            pass
    if "out_time" in fields:
        # HH:MM:SS.micro
        t = fields["out_time"]
        try:
            parts = t.split(":")
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
        except ValueError:
            return None
    return None


def build_ffmpeg_cmd(
    ffmpeg: Path,
    intermediate: Path,
    source: Path,
    dest: Path,
    *,
    crf: int,
    preset: str,
    map_audio: bool,
) -> list[str]:
    cmd = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-nostats",
        "-progress",
        "pipe:1",
        "-i",
        str(intermediate),
        "-i",
        str(source),
        "-map",
        "0:v:0",
    ]
    if map_audio:
        cmd += ["-map", "1:a:0?", "-c:a", "copy"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v",
        "libx265",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-tag:v",
        "hvc1",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    return cmd


def encode_mp4(
    ffmpeg: Path,
    intermediate: Path,
    source: Path,
    dest: Path,
    *,
    crf: int,
    preset: str,
    has_audio: bool,
    duration: float = 0.0,
    cancel_event: threading.Event | None = None,
    log: LogCb | None = None,
    progress: ProgressCb | None = None,
) -> list[str]:
    """Encode the intermediate with thesis FFmpeg. Returns the command that succeeded.

    CRF on the delivered file is 12 unless the operator unlocked it.
    If audio copy fails, retry video-only. Never re-encode audio as a blocker.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() == source.resolve():
        raise EncodeError(f"refusing to overwrite source: {source}")

    attempts: list[tuple[str, bool]] = []
    if has_audio:
        attempts.append(("video + audio copy", True))
        attempts.append(("video only (audio copy failed)", False))
    else:
        attempts.append(("video only (no source audio)", False))

    last_err = ""
    for label, map_audio in attempts:
        if cancel_event is not None and cancel_event.is_set():
            raise EncodeError("cancelled")
        cmd = build_ffmpeg_cmd(
            ffmpeg,
            intermediate,
            source,
            dest,
            crf=crf,
            preset=preset,
            map_audio=map_audio,
        )
        if log:
            log(f"ffmpeg ({label}): {format_cmd(cmd)}")

        acc: dict[str, str] = {}

        def on_stdout(line: str, _acc=acc) -> None:
            parsed = parse_ffmpeg_progress(line)
            if not parsed:
                return
            _acc.update(parsed)
            if progress is None:
                return
            pct: float | None = None
            t = _out_time_seconds(_acc)
            if t is not None and duration > 0:
                pct = max(0.0, min(100.0, 100.0 * t / duration))
            frame = _acc.get("frame", "")
            detail = f"ffmpeg frame {frame}" if frame else "ffmpeg"
            if t is not None:
                detail += f"  t={t:.1f}s"
            progress(detail, pct)

        def on_stderr(line: str) -> None:
            if log and line.strip():
                # Keep ffmpeg chatter short in the UI log.
                low = line.lower()
                if any(k in low for k in ("error", "warning", "failed", "x265")):
                    log(f"  ffmpeg: {line}")

        rc, out_lines, err_lines = run_logged(
            cmd,
            cancel_event=cancel_event,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            hide_window=True,
        )
        if cancel_event is not None and cancel_event.is_set():
            raise EncodeError("cancelled")
        if rc == 0 and dest.is_file() and dest.stat().st_size > 0:
            if log:
                log(f"ffmpeg ok ({label}) → {dest.name} ({dest.stat().st_size} bytes)")
            return cmd
        last_err = "\n".join(err_lines[-40:] + out_lines[-10:]).strip() or f"exit {rc}"
        if log:
            log(f"ffmpeg failed ({label}): {last_err[-500:]}")
        if dest.is_file():
            try:
                dest.unlink()
            except OSError:
                pass
        if not map_audio:
            break
        if log:
            log("audio map/copy failed; retrying without audio (never re-encode audio)")

    raise EncodeError(f"ffmpeg encode failed: {last_err[-800:]}")
