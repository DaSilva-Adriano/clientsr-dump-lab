"""dump_manifest.csv written to the output folder after each batch."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "dump_manifest.csv"
FIELDS = (
    "source",
    "token",
    "output_path",
    "wxh_in",
    "wxh_out",
    "seconds",
    "status",
    "error",
)


@dataclass
class ManifestRow:
    source: str
    token: str
    output_path: str
    wxh_in: str
    wxh_out: str
    seconds: str
    status: str
    error: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "token": self.token,
            "output_path": self.output_path,
            "wxh_in": self.wxh_in,
            "wxh_out": self.wxh_out,
            "seconds": self.seconds,
            "status": self.status,
            "error": self.error,
        }


def write_manifest(output_dir: Path, rows: list[ManifestRow]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / MANIFEST_NAME
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())
    return path
