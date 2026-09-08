"""Plain mpv + GLSL backend (FSRCNNX and Anime4K Fast Mode A)."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path

from app.catalog import ANIME4K_FAST_MODE_A, ModelSpec
from app.winproc import format_cmd, run_logged

LogCb = Callable[[str], None]
ProgressCb = Callable[[str, float | None], None]


class BackendError(RuntimeError):
    pass


# Trailing quality letter used by Anime4K filenames.
_QUALITY = ("UL", "VL", "HQ", "S", "M", "L")


def _list_shaders(shaders_dir: Path) -> list[Path]:
    if not shaders_dir.is_dir():
        return []
    out: list[Path] = []
    try:
        for child in shaders_dir.iterdir():
            if child.is_file() and child.suffix.lower() in {".glsl", ".hook"}:
                out.append(child)
    except OSError:
        return []
    return out


def resolve_shader(shaders_dir: Path, expected: str, *, fuzzy: bool) -> Path | None:
    """Resolve a shader file. FSRCNNX is exact (case-insensitive). Anime4K may fuzzy-match."""
    files = _list_shaders(shaders_dir)
    if not files:
        return None
    expected_l = expected.lower()
    for f in files:
        if f.name.lower() == expected_l:
            return f
    if not fuzzy:
        return None

    stem = Path(expected).stem  # e.g. Anime4K_Restore_CNN_M
    prefix_hits = [f for f in files if f.stem.lower().startswith(stem.lower())]
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    if prefix_hits:
        return min(prefix_hits, key=lambda p: len(p.name))

    # Inserted-word match: Anime4K_Restore_CNN_M → Anime4K_Restore_CNN_Moderate_M
    parts = stem.split("_")
    quality = None
    prefix_parts = parts
    for q in _QUALITY:
        if parts and parts[-1].upper() == q:
            quality = q
            prefix_parts = parts[:-1]
            break
    prefix = "_".join(prefix_parts).lower() + "_"
    hits: list[Path] = []
    for f in files:
        s = f.stem.lower()
        if not s.startswith(prefix):
            continue
        if quality is None:
            hits.append(f)
            continue
        if s.endswith("_" + quality.lower()) or s.endswith(quality.lower()):
            # Reject a stronger quality class accidentally matching a shorter letter
            # e.g. CNN_M must not pick CNN_VL.
            tail = s[len(prefix) :]
            if quality.lower() == "m" and re.search(r"(^|_)(vl|ul|hq|l)($|_)", tail):
                if not re.search(r"(^|_)m($|_)", tail):
                    continue
            hits.append(f)
    if len(hits) == 1:
        return hits[0]
    if hits:
        return min(hits, key=lambda p: len(p.name))
    return None


def resolve_shader_chain(
    shaders_dir: Path,
    expected: tuple[str, ...],
    *,
    fuzzy: bool,
) -> tuple[list[Path], list[str]]:
    """Return (resolved paths, human notes). Raises BackendError if any file is missing."""
    if not shaders_dir.is_dir():
        raise BackendError(
            f"shaders directory missing: {shaders_dir}"
        )
    resolved: list[Path] = []
    notes: list[str] = []
    missing: list[str] = []
    present = [p.name for p in _list_shaders(shaders_dir)]
    for name in expected:
        hit = resolve_shader(shaders_dir, name, fuzzy=fuzzy)
        if hit is None:
            missing.append(name)
            continue
        resolved.append(hit)
        if hit.name != name:
            notes.append(f"{name} → {hit.name}")
        else:
            notes.append(hit.name)
    if missing:
        listing = ", ".join(present) if present else "(directory empty)"
        raise BackendError(
            "shader chain could not be resolved. missing: "
            + ", ".join(missing)
            + f". expected in {shaders_dir}. present: {listing}"
        )
    return resolved, notes


def glsl_shaders_arg(paths: list[Path]) -> str:
    """Windows mpv path-list separator is ';'."""
    return ";".join(str(p) for p in paths)


def build_mpv_glsl_cmd(
    mpv: Path,
    source: Path,
    tmp_mkv: Path,
    shaders: list[Path],
    target_w: int,
    target_h: int,
    fps_str: str,
) -> list[str]:
    """Plain mpv, no user config, absolute shader paths, locked 2× canvas."""
    cmd = [
        str(mpv),
        "--no-config",
        "--keep-open=no",
        "--idle=no",
        "--no-audio",
        "--aid=no",
        "--sid=no",
        "--no-sub",
        "--osc=no",
        "--osd-level=0",
        "--vo=gpu-next",
        "--gpu-api=auto",
        "--hwdec=auto-copy",
        "--force-window=immediate",
        "--geometry=320x180",
        "--untimed",
        "--framedrop=no",
        f"--glsl-shaders={glsl_shaders_arg(shaders)}",
        # Current mpv: size lock is vf=gpu (vo_gpu as filter). vf=gpu-next is not a filter.
        f"--vf=gpu=w={target_w}:h={target_h}",
        f"--o={tmp_mkv}",
        "--of=matroska",
        "--ovc=libx264",
        "--ovcopts=crf=16,preset=fast",
    ]
    # --ofps/--oautofps were removed from mpv 0.41 encoding; timestamps follow the source.
    cmd.append(str(source))
    return cmd


_PCT_RE = re.compile(r"\((\d{1,3})%\)")
_AV_RE = re.compile(r"AV:\s*([0-9:.]+)\s*/\s*([0-9:.]+)")


def _parse_mpv_progress(line: str) -> tuple[str, float | None]:
    pct: float | None = None
    m = _PCT_RE.search(line)
    if m:
        try:
            pct = float(m.group(1))
        except ValueError:
            pct = None
    detail = line.strip()
    if len(detail) > 180:
        detail = detail[:180] + "…"
    return detail, pct


def render_glsl(
    mpv: Path,
    source: Path,
    tmp_mkv: Path,
    spec: ModelSpec,
    shaders_dir: Path,
    target_w: int,
    target_h: int,
    fps_str: str,
    *,
    cancel_event: threading.Event | None = None,
    log: LogCb | None = None,
    progress: ProgressCb | None = None,
) -> tuple[list[str], list[Path]]:
    """Render one GLSL model to a near-lossless intermediate MKV.

    Does not stub: missing shaders raise BackendError with the expected names.
    """
    if not mpv.is_file():
        raise BackendError(f"plain mpv missing: {mpv}")
    fuzzy = spec.token.startswith("ANIME4K")
    shaders, notes = resolve_shader_chain(shaders_dir, spec.shaders, fuzzy=fuzzy)
    if log:
        log(f"{spec.token} shaders: " + " → ".join(notes))

    tmp_mkv.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_mpv_glsl_cmd(
        mpv, source, tmp_mkv, shaders, target_w, target_h, fps_str
    )
    if log:
        log(f"mpv: {format_cmd(cmd)}")

    def on_err(line: str) -> None:
        if not line.strip():
            return
        detail, pct = _parse_mpv_progress(line)
        if progress is not None and (
            "%" in line or line.startswith("(") or "AV:" in line or "frame" in line.lower()
        ):
            progress(f"mpv {detail}", pct)
        elif log and any(
            k in line.lower()
            for k in ("error", "fatal", "failed", "cannot", "no such", "option")
        ):
            log(f"  mpv: {line}")

    rc, _out, err = run_logged(
        cmd,
        cancel_event=cancel_event,
        on_stderr=on_err,
        on_stdout=on_err,
        hide_window=False,
    )
    if cancel_event is not None and cancel_event.is_set():
        raise BackendError("cancelled")
    if rc != 0 or not tmp_mkv.is_file() or tmp_mkv.stat().st_size == 0:
        tail = "\n".join(err[-30:]).strip()
        # Fallback: some mpv builds expose vf=gpu rather than vf=gpu-next.
        if rc != 0 and any(
            s in tail.lower()
            for s in ("isn't supported", "doesn't exist", "option vf", "option not found")
        ):
            if log:
                log("mpv vf=gpu not accepted; retrying --vf=gpu-next=w:h")
            cmd2 = [
                a
                if not a.startswith("--vf=gpu=")
                else f"--vf=gpu-next=w={target_w}:h={target_h}"
                for a in cmd
            ]
            if log:
                log(f"mpv: {format_cmd(cmd2)}")
            rc, _out, err = run_logged(
                cmd2,
                cancel_event=cancel_event,
                on_stderr=on_err,
                on_stdout=on_err,
                hide_window=False,
            )
            cmd = cmd2
            if cancel_event is not None and cancel_event.is_set():
                raise BackendError("cancelled")
            if rc == 0 and tmp_mkv.is_file() and tmp_mkv.stat().st_size > 0:
                return cmd, shaders
            tail = "\n".join(err[-30:]).strip()
        raise BackendError(
            f"mpv render failed for {spec.token} (exit {rc}). "
            f"output={tmp_mkv} "
            + (tail[-800:] if tail else "no stderr")
        )
    return cmd, shaders


def model_ready_glsl(
    spec: ModelSpec, mpv: Path, shaders_dir: Path
) -> str | None:
    """Return an error string if this GLSL model cannot run, else None."""
    if not mpv.is_file():
        return f"{spec.token}: plain mpv missing: {mpv}"
    if not shaders_dir.is_dir():
        return f"{spec.token}: shaders directory missing: {shaders_dir}"
    try:
        resolve_shader_chain(
            shaders_dir, spec.shaders, fuzzy=spec.token.startswith("ANIME4K")
        )
    except BackendError as exc:
        return f"{spec.token}: {exc}"
    return None
