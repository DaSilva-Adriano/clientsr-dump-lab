"""Persisted settings in %LOCALAPPDATA%\\ClientSRDumpLab\\config.yaml."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app import APP_ID

DEFAULT_FFMPEG = r"C:\VSR\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe"
DEFAULT_FFPROBE = r"C:\VSR\ffmpeg-9.0.1-full_build\bin\ffprobe.exe"
DEFAULT_MPV = r"C:\Tools\mpv\mpv.exe"
DEFAULT_MPV_PORTABLE = r"C:\Tools\mpv\portable_config"
DEFAULT_ANIMEJANAI = r"C:\Tools\mpv-AnimeJaNai\mpv.exe"
DEFAULT_SHADERS = r"C:\Tools\mpv\portable_config\shaders"
DEFAULT_X265_PRESET = "medium"
DEFAULT_CRF = 12
THESIS_CRF = 12


def appdata_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(local) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return appdata_dir() / "config.yaml"


def tmp_dir() -> Path:
    path = appdata_dir() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    w = Path("W:/")
    if w.exists():
        return Path("W:/dumps")
    return Path.home() / "Videos" / "ClientSRDump"


@dataclass
class AppConfig:
    ffmpeg: str = DEFAULT_FFMPEG
    ffprobe: str = DEFAULT_FFPROBE
    mpv: str = DEFAULT_MPV
    mpv_portable_config: str = DEFAULT_MPV_PORTABLE
    animejanai_mpv: str = DEFAULT_ANIMEJANAI
    shaders_dir: str = DEFAULT_SHADERS
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    x265_preset: str = DEFAULT_X265_PRESET
    crf: int = DEFAULT_CRF
    crf_unlocked: bool = False
    two_parallel_glsl: bool = False
    animejanai_engine_note: bool = False

    def ffmpeg_path(self) -> Path:
        return Path(self.ffmpeg)

    def ffprobe_path(self) -> Path:
        return Path(self.ffprobe)

    def mpv_path(self) -> Path:
        return Path(self.mpv)

    def mpv_portable_path(self) -> Path:
        return Path(self.mpv_portable_config)

    def animejanai_path(self) -> Path:
        return Path(self.animejanai_mpv)

    def shaders_path(self) -> Path:
        return Path(self.shaders_dir)

    def output_path(self) -> Path:
        return Path(self.output_dir)

    def effective_crf(self) -> int:
        if self.crf_unlocked:
            return int(self.crf)
        return THESIS_CRF


def _as_str(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    return str(value)


def load_config() -> AppConfig:
    path = config_path()
    if not path.is_file():
        cfg = AppConfig()
        save_config(cfg)
        return cfg
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    cfg = AppConfig()
    cfg.ffmpeg = _as_str(raw.get("ffmpeg"), cfg.ffmpeg)
    cfg.ffprobe = _as_str(raw.get("ffprobe"), cfg.ffprobe)
    cfg.mpv = _as_str(raw.get("mpv"), cfg.mpv)
    cfg.mpv_portable_config = _as_str(
        raw.get("mpv_portable_config"), cfg.mpv_portable_config
    )
    cfg.animejanai_mpv = _as_str(raw.get("animejanai_mpv"), cfg.animejanai_mpv)
    cfg.shaders_dir = _as_str(raw.get("shaders_dir"), cfg.shaders_dir)
    cfg.output_dir = _as_str(raw.get("output_dir"), cfg.output_dir)
    cfg.x265_preset = _as_str(raw.get("x265_preset"), cfg.x265_preset) or DEFAULT_X265_PRESET
    try:
        cfg.crf = int(raw.get("crf", DEFAULT_CRF))
    except (TypeError, ValueError):
        cfg.crf = DEFAULT_CRF
    cfg.crf_unlocked = bool(raw.get("crf_unlocked", False))
    cfg.two_parallel_glsl = bool(raw.get("two_parallel_glsl", False))
    cfg.animejanai_engine_note = bool(raw.get("animejanai_engine_note", False))
    return cfg


def save_config(cfg: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8")


def _usable_animejanai_binary(path: Path) -> bool:
    """Reject tiny launch stubs (e.g. mpvnet.com copied to mpv.exe) that re-exec themselves."""
    try:
        if not path.is_file():
            return False
    except OSError:
        return False
    if path.name.lower() == "mpv.exe" and path.stat().st_size < 1_000_000:
        return False
    return True


def resolve_animejanai_binary(configured: Path) -> Path | None:
    """Prefer the console host (mpvnet.com), then mpvnet.exe, then a real mpv.exe."""
    if configured.suffix.lower() in {".exe", ".com"}:
        folder = configured.parent
    else:
        folder = configured
    candidates = [
        configured,
        folder / "mpvnet.com",
        folder / "mpvnet.exe",
        folder / "mpv.exe",
    ]
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand).lower()
        if key in seen:
            continue
        seen.add(key)
        if _usable_animejanai_binary(cand):
            return cand
    return None


def animejanai_portable_config(binary: Path) -> Path:
    return binary.parent / "portable_config"
