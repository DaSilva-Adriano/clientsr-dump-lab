"""Windows subprocess helpers. Never shell=True. Always argv lists."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


def format_cmd(argv: Sequence[str]) -> str:
    """Pretty-print an argv list for the log (display only, not executed)."""
    parts: list[str] = []
    for raw in argv:
        a = str(raw)
        if not a or any(c in a for c in ' \t&()[]{}^=;!\'+,`~'):
            parts.append('"' + a.replace('"', r"\"") + '"')
        else:
            parts.append(a)
    return " ".join(parts)


def popen(
    argv: Sequence[str],
    *,
    hide_window: bool = True,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    flags = CREATE_NEW_PROCESS_GROUP
    if hide_window:
        flags |= CREATE_NO_WINDOW
    return subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        creationflags=flags,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill the process group on Windows via taskkill /PID /T."""
    if proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        )
    except Exception:
        try:
            proc.kill()
        except OSError:
            pass


def run_capture(
    argv: Sequence[str],
    *,
    timeout: float | None = 60,
    hide_window: bool = True,
) -> subprocess.CompletedProcess[str]:
    flags = CREATE_NEW_PROCESS_GROUP
    if hide_window:
        flags |= CREATE_NO_WINDOW
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
    )


def wait_with_pump(
    proc: subprocess.Popen[str],
    *,
    cancel_event: threading.Event | None = None,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    poll_s: float = 0.2,
) -> int:
    """Read stdout/stderr on helper threads, honour cancel, return exit code."""

    def _read(stream, cb: Callable[[str], None] | None) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                if cb is not None:
                    cb(line.rstrip("\r\n"))
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    t_out = threading.Thread(
        target=_read, args=(proc.stdout, on_stdout), daemon=True, name="stdout-pump"
    )
    t_err = threading.Thread(
        target=_read, args=(proc.stderr, on_stderr), daemon=True, name="stderr-pump"
    )
    t_out.start()
    t_err.start()

    cancelled = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            kill_tree(proc)
            break
        try:
            proc.wait(timeout=poll_s)
            break
        except subprocess.TimeoutExpired:
            continue

    t_out.join(timeout=2.0)
    t_err.join(timeout=2.0)
    code = proc.poll()
    if cancelled:
        return 1 if code is None else code
    return 0 if code is None else code


def run_logged(
    argv: Sequence[str],
    *,
    cancel_event: threading.Event | None = None,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    hide_window: bool = True,
    cwd: Path | str | None = None,
) -> tuple[int, list[str], list[str]]:
    """Run argv, return (rc, stdout_lines, stderr_lines)."""
    out_lines: list[str] = []
    err_lines: list[str] = []

    def _out(line: str) -> None:
        out_lines.append(line)
        if on_stdout is not None:
            on_stdout(line)

    def _err(line: str) -> None:
        err_lines.append(line)
        if on_stderr is not None:
            on_stderr(line)

    proc = popen(argv, hide_window=hide_window, cwd=cwd)
    rc = wait_with_pump(
        proc,
        cancel_event=cancel_event,
        on_stdout=_out,
        on_stderr=_err,
    )
    return rc, out_lines, err_lines
