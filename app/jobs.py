"""Expand queue × selected models and run dumps sequentially (optional 2 GLSL workers)."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.backend_animejanai import (
    ENGINE_NOTE,
    AnimeJaNaiError,
    model_ready_animejanai,
    render_animejanai,
)
from app.backend_mpv import BackendError, model_ready_glsl, render_glsl
from app.catalog import ModelSpec
from app.encode import EncodeError, encode_mp4
from app.manifest import ManifestRow
from app.naming import assert_not_source, output_path, sidecar_path
from app.probe import probe_output_height
from app.settings import AppConfig, tmp_dir

LogCb = Callable[[str], None]
ProgressCb = Callable[[int, int, str, str, float | None], None]
JobStatusCb = Callable[[str, str, str], None]


@dataclass
class QueueItem:
    path: Path
    width: int
    height: int
    fps: float
    fps_str: str
    duration: float
    has_audio: bool
    height_class: str
    target_w: int | None
    target_h: int | None
    content_tag: str = "Live"
    force_target: str = ""
    status: str = ""
    reason: str = ""
    fps_warning: bool = False
    iid: str = ""


@dataclass
class DumpJob:
    item: QueueItem
    model: ModelSpec
    dest: Path
    sidecar: Path
    skip_exists: bool = False


@dataclass
class JobResult:
    job: DumpJob
    status: str
    error: str = ""
    elapsed: float = 0.0
    output_bytes: int = 0
    backend_cmd: list[str] = field(default_factory=list)
    ffmpeg_cmd: list[str] = field(default_factory=list)
    start_utc: str = ""
    end_utc: str = ""


def expand_jobs(
    items: list[QueueItem],
    models: list[ModelSpec],
    output_dir: Path,
    overwrite: bool,
) -> list[DumpJob]:
    jobs: list[DumpJob] = []
    for item in items:
        if item.target_w is None or item.target_h is None:
            continue
        for model in models:
            dest = output_path(output_dir, item.path, model.token)
            assert_not_source(item.path, dest)
            exists = dest.is_file() and dest.stat().st_size > 0
            jobs.append(
                DumpJob(
                    item=item,
                    model=model,
                    dest=dest,
                    sidecar=sidecar_path(dest),
                    skip_exists=exists and not overwrite,
                )
            )
    return jobs


def job_matrix_text(items: list[QueueItem], models: list[ModelSpec]) -> str:
    eligible = [
        i for i in items if i.target_w is not None and i.target_h is not None
    ]
    n_files = len(eligible)
    n_models = len(models)
    n_out = n_files * n_models
    tokens = ", ".join(m.token for m in models) if models else "(none)"
    skipped = len(items) - n_files
    extra = f"  ·  {skipped} unsupported skipped" if skipped else ""
    return (
        f"{n_files} files × {n_models} models = {n_out} outputs"
        f"{extra}\nTokens: {tokens}"
    )


def validate_models_ready(models: list[ModelSpec], cfg: AppConfig) -> list[str]:
    """Refuse a model whose binary or shader is missing. Never silently skip."""
    errors: list[str] = []
    mpv = cfg.mpv_path()
    shaders = cfg.shaders_path()
    for spec in models:
        if spec.backend == "mpv_glsl":
            err = model_ready_glsl(spec, mpv, shaders)
            if err:
                errors.append(err)
        elif spec.backend == "animejanai":
            err = model_ready_animejanai(cfg.animejanai_path())
            if err:
                errors.append(err)
        else:
            errors.append(f"{spec.token}: unknown backend {spec.backend}")
    return errors


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tmp_mkv(stem: str, token: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)[:80]
    name = f"{safe}-{token}-{os.getpid()}-{uuid.uuid4().hex[:8]}.mkv"
    return tmp_dir() / name


def _write_sidecar(result: JobResult, cfg: AppConfig) -> None:
    job = result.job
    item = job.item
    payload = {
        "source_path": str(item.path),
        "source_wxh": f"{item.width}x{item.height}",
        "target_wxh": f"{item.target_w}x{item.target_h}",
        "fps": item.fps,
        "fps_str": item.fps_str,
        "token": job.model.token,
        "device_label": job.model.device_label,
        "device_tag": job.model.device_tag,
        "content_group": job.model.content_group,
        "content_tag": item.content_tag,
        "backend": job.model.backend,
        "backend_command": result.backend_cmd,
        "ffmpeg_encode_command": result.ffmpeg_cmd,
        "start_utc": result.start_utc,
        "end_utc": result.end_utc,
        "elapsed_seconds": round(result.elapsed, 3),
        "output_size_bytes": result.output_bytes,
        "output_path": str(job.dest),
        "crf": cfg.effective_crf(),
        "x265_preset": cfg.x265_preset,
        "status": result.status,
        "error": result.error,
    }
    job.sidecar.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class BatchRunner:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        dry_run: bool,
        parallel_glsl: bool,
        log: LogCb,
        progress: ProgressCb,
        job_status: JobStatusCb | None = None,
        cancel_event: threading.Event | None = None,
        on_animejanai_started: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.parallel_glsl = parallel_glsl
        self.log = log
        self.progress = progress
        self.job_status = job_status
        self.cancel_event = cancel_event or threading.Event()
        self.on_animejanai_started = on_animejanai_started
        self.results: list[JobResult] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self, jobs: list[DumpJob]) -> list[JobResult]:
        self.results = []
        if not jobs:
            return self.results
        if not self.parallel_glsl:
            for i, job in enumerate(jobs, start=1):
                if self.cancel_event.is_set():
                    self._record_cancel_rest(jobs[i - 1 :])
                    break
                self._run_one(i, len(jobs), job)
            return self.results

        # Two parallel GLSL jobs; AnimeJaNai takes both slots (VRAM).
        sem = threading.Semaphore(2)
        total = len(jobs)
        self.log("parallel GLSL: up to 2 concurrent shader dumps (AnimeJaNai exclusive)")

        def wrapped(idx: int, job: DumpJob) -> None:
            exclusive = job.model.backend == "animejanai"
            acquired = 2 if exclusive else 1
            for _ in range(acquired):
                sem.acquire()
            try:
                if self.cancel_event.is_set():
                    self._store(
                        JobResult(job=job, status="cancelled", error="cancelled")
                    )
                    return
                self._run_one(idx, total, job)
            finally:
                for _ in range(acquired):
                    sem.release()

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dump") as pool:
            futs = [pool.submit(wrapped, i, job) for i, job in enumerate(jobs, start=1)]
            for fut in futs:
                try:
                    fut.result()
                except Exception as exc:
                    self.log(f"worker crashed: {exc}")
        order = {id(job): i for i, job in enumerate(jobs)}
        self.results.sort(key=lambda r: order.get(id(r.job), 10_000))
        return self.results

    def _record_cancel_rest(self, rest: list[DumpJob]) -> None:
        for job in rest:
            self._store(JobResult(job=job, status="cancelled", error="cancelled"))

    def _store(self, result: JobResult) -> None:
        with self._lock:
            self.results.append(result)

    def _notify(self, job: DumpJob, status: str, error: str = "") -> None:
        if self.job_status:
            ident = job.item.iid or str(job.item.path)
            key = f"{ident}::{job.model.token}"
            self.job_status(key, status, error)

    def _run_one(self, index: int, total: int, job: DumpJob) -> None:
        token = job.model.token
        src_name = job.item.path.name
        label = f"[{index}/{total}] {src_name} · {token}"
        self.progress(index, total, token, "starting", None)
        self._notify(job, "running")

        if job.skip_exists:
            self.log(f"{label}: exists — skipped (enable Overwrite existing to redo)")
            result = JobResult(job=job, status="exists", error="exists")
            self._store(result)
            self._notify(job, "exists")
            self.progress(index, total, token, "exists", 100.0)
            return

        start = datetime.now(timezone.utc)
        result = JobResult(job=job, status="running", start_utc=_utc_now())
        tmp = _tmp_mkv(job.item.path.stem, token)

        def log(msg: str) -> None:
            self.log(f"{label}: {msg}")

        def prog(detail: str, pct: float | None) -> None:
            self.progress(index, total, token, detail, pct)

        try:
            if self.dry_run:
                self._dry_run(job, tmp, log)
                result.status = "dry-run"
                result.end_utc = _utc_now()
                result.elapsed = (
                    datetime.now(timezone.utc) - start
                ).total_seconds()
                self._store(result)
                self._notify(job, "dry-run")
                self.progress(index, total, token, "dry-run", 100.0)
                return

            backend_cmd = self._render(job, tmp, log, prog)
            result.backend_cmd = backend_cmd
            if self.cancel_event.is_set():
                raise BackendError("cancelled")

            ffmpeg_cmd = encode_mp4(
                self.cfg.ffmpeg_path(),
                tmp,
                job.item.path,
                job.dest,
                crf=self.cfg.effective_crf(),
                preset=self.cfg.x265_preset,
                has_audio=job.item.has_audio,
                duration=job.item.duration,
                cancel_event=self.cancel_event,
                log=log,
                progress=prog,
            )
            result.ffmpeg_cmd = ffmpeg_cmd

            out_w, out_h = probe_output_height(self.cfg.ffprobe_path(), job.dest)
            if out_h != job.item.target_h or out_w != job.item.target_w:
                raise EncodeError(
                    f"output {out_w}x{out_h} is not the 2× target "
                    f"{job.item.target_w}x{job.item.target_h}"
                )

            result.status = "ok"
            result.output_bytes = job.dest.stat().st_size
            result.end_utc = _utc_now()
            result.elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            _write_sidecar(result, self.cfg)
            self._store(result)
            self._notify(job, "ok")
            log(f"ok {out_w}x{out_h} in {result.elapsed:.1f}s → {job.dest.name}")
            self.progress(index, total, token, "done", 100.0)
        except Exception as exc:
            if self.cancel_event.is_set() or str(exc).lower() == "cancelled":
                result.status = "cancelled"
                result.error = "cancelled"
            else:
                result.status = "failed"
                result.error = str(exc)
            result.end_utc = _utc_now()
            result.elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            self._store(result)
            self._notify(job, result.status, result.error)
            log(f"{result.status}: {result.error}")
            self.progress(index, total, token, result.status, None)
            # Partial dest is useless if validation failed.
            if result.status == "failed" and job.dest.is_file():
                try:
                    # Keep dest if ffmpeg wrote something and the error is probe-after;
                    # still fail the cell. Operator can inspect.
                    pass
                except OSError:
                    pass
        finally:
            if tmp.is_file() and result.status == "ok":
                try:
                    tmp.unlink()
                except OSError as exc:
                    log(f"could not delete tmp {tmp}: {exc}")
            elif tmp.is_file():
                log(f"leaving tmp (cancel/fail): {tmp}")

    def _dry_run(self, job: DumpJob, tmp: Path, log: LogCb) -> None:
        from app.backend_mpv import build_mpv_glsl_cmd, resolve_shader_chain
        from app.backend_animejanai import build_animejanai_cmd, write_balanced_conf
        from app.encode import build_ffmpeg_cmd
        from app.settings import animejanai_portable_config, resolve_animejanai_binary

        item = job.item
        spec = job.model
        tw, th = int(item.target_w or 0), int(item.target_h or 0)
        if spec.backend == "mpv_glsl":
            shaders, notes = resolve_shader_chain(
                self.cfg.shaders_path(),
                spec.shaders,
                fuzzy=spec.token.startswith("ANIME4K"),
            )
            log("shaders: " + " → ".join(notes))
            cmd = build_mpv_glsl_cmd(
                self.cfg.mpv_path(),
                item.path,
                tmp,
                shaders,
                tw,
                th,
                item.fps_str,
            )
            log("mpv (dry-run, not executed): " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        elif spec.backend == "animejanai":
            binary = resolve_animejanai_binary(self.cfg.animejanai_path())
            if binary is None:
                raise AnimeJaNaiError(model_ready_animejanai(self.cfg.animejanai_path()) or "missing")
            portable = animejanai_portable_config(binary)
            include_conf = write_balanced_conf()
            cmd = build_animejanai_cmd(
                binary, portable, include_conf, item.path, tmp, tw, th, item.fps_str
            )
            log(ENGINE_NOTE)
            log("mpv (dry-run, not executed): " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        ff = build_ffmpeg_cmd(
            self.cfg.ffmpeg_path(),
            tmp,
            item.path,
            job.dest,
            crf=self.cfg.effective_crf(),
            preset=self.cfg.x265_preset,
            map_audio=item.has_audio,
        )
        from app.winproc import format_cmd

        log("ffmpeg (dry-run, not executed): " + format_cmd(ff))

    def _render(
        self,
        job: DumpJob,
        tmp: Path,
        log: LogCb,
        prog: Callable[[str, float | None], None],
    ) -> list[str]:
        item = job.item
        spec = job.model
        tw, th = int(item.target_w or 0), int(item.target_h or 0)
        if spec.backend == "mpv_glsl":
            cmd, _shaders = render_glsl(
                self.cfg.mpv_path(),
                item.path,
                tmp,
                spec,
                self.cfg.shaders_path(),
                tw,
                th,
                item.fps_str,
                cancel_event=self.cancel_event,
                log=log,
                progress=prog,
            )
            return cmd
        if spec.backend == "animejanai":
            if self.on_animejanai_started:
                self.on_animejanai_started()
            cmd = render_animejanai(
                self.cfg.animejanai_path(),
                item.path,
                tmp,
                tw,
                th,
                item.fps_str,
                cancel_event=self.cancel_event,
                log=log,
                progress=prog,
            )
            return cmd
        raise BackendError(f"unknown backend {spec.backend}")


def results_to_manifest(results: list[JobResult]) -> list[ManifestRow]:
    rows: list[ManifestRow] = []
    for r in results:
        item = r.job.item
        wxh_out = (
            f"{item.target_w}x{item.target_h}"
            if item.target_w and item.target_h
            else ""
        )
        rows.append(
            ManifestRow(
                source=str(item.path),
                token=r.job.model.token,
                output_path=str(r.job.dest),
                wxh_in=f"{item.width}x{item.height}",
                wxh_out=wxh_out,
                seconds=f"{r.elapsed:.3f}",
                status=r.status,
                error=r.error,
            )
        )
    return rows
