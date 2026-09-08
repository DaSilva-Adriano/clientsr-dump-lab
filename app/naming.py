"""Output filename: original stem + '-' + token + '.mp4'."""

from __future__ import annotations

from pathlib import Path

from app.catalog import ALLOWED_DEVICE_TAGS, MODELS_BY_TOKEN


class NamingError(ValueError):
    pass


def original_stem(source: Path | str) -> str:
    """Everything before the last dot of the file name. Stem is kept unchanged."""
    name = Path(source).name
    if "." not in name:
        return name
    return name.rsplit(".", 1)[0]


def output_name(source: Path | str, token: str) -> str:
    token = (token or "").strip()
    if not token:
        raise NamingError("empty token")
    if " " in token:
        raise NamingError(f"token must not contain spaces: {token!r}")
    if token != token.upper():
        raise NamingError(f"token must be uppercase: {token!r}")
    if token not in MODELS_BY_TOKEN:
        raise NamingError(f"unknown token {token!r}")
    spec = MODELS_BY_TOKEN[token]
    if spec.device_tag not in ALLOWED_DEVICE_TAGS:
        raise NamingError(f"illegal device tag {spec.device_tag!r}")
    return f"{original_stem(source)}-{token}.mp4"


def output_path(output_dir: Path | str, source: Path | str, token: str) -> Path:
    return Path(output_dir) / output_name(source, token)


def sidecar_path(mp4: Path | str) -> Path:
    p = Path(mp4)
    return p.with_suffix(".dump.json")


def assert_not_source(source: Path, dest: Path) -> None:
    try:
        if source.resolve() == dest.resolve():
            raise NamingError(
                f"refusing to overwrite source file: {source}"
            )
    except OSError:
        if str(source).lower() == str(dest).lower():
            raise NamingError(
                f"refusing to overwrite source file: {source}"
            )
