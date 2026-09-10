"""ClientSR Dump Lab — CustomTkinter dark-theme GUI."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import customtkinter as ctk

from app import APP_NAME
from app.backend_animejanai import ENGINE_NOTE
from app.backend_live import (
    LIVE_NONE_LABEL,
    LiveError,
    LiveLaunch,
    TOKEN_LIVE_NONE,
    cmd_with_hwdec,
    is_hwdec_init_failure,
    model_ready_live,
    normalize_live_hwdec,
    parse_attached_hwdec,
    prepare_live,
    start_live_process,
)
from app.catalog import GROUP_A, GROUP_B, MODELS, MODELS_BY_TOKEN, ModelSpec, selected_models
from app.jobs import (
    BatchRunner,
    DumpJob,
    QueueItem,
    expand_jobs,
    job_matrix_text,
    results_to_manifest,
    validate_models_ready,
)
from app.manifest import write_manifest
from app.probe import (
    CLASS_UNSUPPORTED,
    VIDEO_EXTENSIONS,
    collect_videos,
    ffmpeg_has_libx265,
    format_duration,
    format_fps,
    fps_is_standard,
    gpu_name,
    classify_height,
    probe_file,
    probe_tools,
)
from app.settings import (
    DEFAULT_LIVE_HWDEC,
    LIVE_HWDEC_CHOICES,
    THESIS_CRF,
    AppConfig,
    appdata_dir,
    load_config,
    save_config,
    tmp_dir,
)
from app.winproc import format_cmd, kill_tree

COLS = ("file", "wxh", "fps", "duration", "cls", "content", "status")
COL_HEAD = {
    "file": "File",
    "wxh": "WxH",
    "fps": "fps",
    "duration": "Duration",
    "cls": "Class",
    "content": "Content",
    "status": "Status",
}
COL_W = {
    "file": 280,
    "wxh": 100,
    "fps": 64,
    "duration": 80,
    "cls": 150,
    "content": 90,
    "status": 220,
}

FORCE_VALUES = ("Off (auto)", "720p", "2160p")


def _live_none_label() -> str:
    return f"{LIVE_NONE_LABEL}  [{TOKEN_LIVE_NONE}]"


def _live_model_labels() -> list[str]:
    return [_live_none_label()] + [f"{m.ui_label}  [{m.token}]" for m in MODELS]


X265_PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)


def _parse_drop_data(data: str) -> list[Path]:
    if not data:
        return []
    found = re.findall(r"\{([^}]+)\}|([^\s{]+)", data)
    out: list[Path] = []
    for braced, bare in found:
        raw = braced or bare
        if raw:
            out.append(Path(raw))
    return out


def _create_root():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    dnd_token = None
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD

        class CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.TkdndVersion = TkinterDnD._require(self)

        root = CTkDnD()
        dnd_token = DND_FILES
    except Exception:
        root = ctk.CTk()
    return root, dnd_token


def _style_treeview() -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    bg = "#1a1a1a"
    fg = "#e6e6e6"
    heading = "#242424"
    select = "#1f6aa5"
    style.configure(
        "Dump.Treeview",
        background=bg,
        foreground=fg,
        fieldbackground=bg,
        rowheight=26,
        borderwidth=0,
        font=("Segoe UI", 10),
    )
    style.configure(
        "Dump.Treeview.Heading",
        background=heading,
        foreground=fg,
        relief="flat",
        font=("Segoe UI", 10, "bold"),
    )
    style.map(
        "Dump.Treeview",
        background=[("selected", select)],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Dump.Vertical.TScrollbar",
        background="#2b2b2b",
        troughcolor="#1a1a1a",
        arrowcolor=fg,
    )


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master: "DumpLabApp") -> None:
        super().__init__(master.root)
        self.master_app = master
        self.title("Settings — ClientSR Dump Lab")
        self.geometry("820x760")
        self.minsize(720, 640)
        self.transient(master.root)
        self.grab_set()
        self.cfg = master.cfg

        self.entries: dict[str, ctk.CTkEntry] = {}
        pad = {"padx": 12, "pady": 4}

        ctk.CTkLabel(
            self,
            text="Tool paths (persisted in %LOCALAPPDATA%\\ClientSRDumpLab\\config.yaml)",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(16, 8))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=12)

        fields = [
            ("ffmpeg", "ffmpeg", True),
            ("ffprobe", "ffprobe", True),
            ("mpv", "Plain mpv (GLSL)", True),
            ("mpv_portable_config", "mpv portable_config", False),
            ("animejanai_mpv", "AnimeJaNai mpv", True),
            ("shaders_dir", "Shaders directory", False),
        ]
        for i, (key, label, is_file) in enumerate(fields):
            ctk.CTkLabel(form, text=label, width=170, anchor="w").grid(
                row=i, column=0, sticky="w", **pad
            )
            ent = ctk.CTkEntry(form, width=480)
            ent.grid(row=i, column=1, sticky="ew", **pad)
            ent.insert(0, getattr(self.cfg, key))
            self.entries[key] = ent
            ctk.CTkButton(
                form,
                text="Browse",
                width=80,
                command=lambda k=key, f=is_file: self._browse(k, f),
            ).grid(row=i, column=2, **pad)
        form.grid_columnconfigure(1, weight=1)

        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(opts, text="x265 preset").grid(row=0, column=0, sticky="w", padx=4)
        self.preset = ctk.CTkComboBox(opts, values=list(X265_PRESETS), width=140)
        self.preset.set(self.cfg.x265_preset if self.cfg.x265_preset in X265_PRESETS else "medium")
        self.preset.grid(row=0, column=1, padx=8)

        self.unlock_crf = ctk.BooleanVar(value=self.cfg.crf_unlocked)
        self.crf_entry = ctk.CTkEntry(opts, width=70)
        self.crf_entry.insert(0, str(self.cfg.crf if self.cfg.crf_unlocked else THESIS_CRF))
        ctk.CTkCheckBox(
            opts,
            text="Unlock CRF",
            variable=self.unlock_crf,
            command=self._on_unlock,
        ).grid(row=0, column=2, padx=12)
        ctk.CTkLabel(opts, text="CRF").grid(row=0, column=3, sticky="w")
        self.crf_entry.grid(row=0, column=4, padx=8)
        self._sync_crf_entry()

        self.parallel = ctk.BooleanVar(value=self.cfg.two_parallel_glsl)
        ctk.CTkCheckBox(
            opts,
            text="Two parallel GLSL jobs (VRAM — off by default)",
            variable=self.parallel,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0), padx=4)

        ctk.CTkLabel(opts, text="Live hwdec").grid(
            row=2, column=0, sticky="w", padx=4, pady=(10, 0)
        )
        self.live_hwdec = ctk.CTkComboBox(
            opts, values=list(LIVE_HWDEC_CHOICES), width=140
        )
        current_hwdec = (
            self.cfg.live_hwdec
            if self.cfg.live_hwdec in LIVE_HWDEC_CHOICES
            else DEFAULT_LIVE_HWDEC
        )
        self.live_hwdec.set(current_hwdec)
        self.live_hwdec.grid(row=2, column=1, padx=8, pady=(10, 0))
        ctk.CTkLabel(
            opts,
            text="AI live only (dumps ignore). LIVE_NONE stays nvdec (no copy) unless the box below is checked.",
            text_color="#8a8a8a",
            wraplength=420,
            justify="left",
        ).grid(row=2, column=2, columnspan=3, sticky="w", padx=8, pady=(10, 0))

        self.none_force_copy = ctk.BooleanVar(value=self.cfg.live_none_force_copy)
        ctk.CTkCheckBox(
            opts,
            text="Force copy on None baseline",
            variable=self.none_force_copy,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0), padx=4)
        ctk.CTkLabel(
            opts,
            text="Default off. Makes LIVE_NONE use nvdec-copy so the no-AI baseline pays the same GPU copy as shader models.",
            text_color="#8a8a8a",
            wraplength=420,
            justify="left",
        ).grid(row=3, column=2, columnspan=3, sticky="w", padx=8, pady=(8, 0))

        warn = ctk.CTkLabel(
            self,
            text="Thesis protocol is CRF 12 on the delivered MP4. Unlocking changes the comparison.",
            text_color="#c9a227",
            wraplength=760,
            justify="left",
        )
        warn.pack(anchor="w", padx=16)

        self.status_box = ctk.CTkTextbox(self, height=160, font=ctk.CTkFont(family="Consolas", size=12))
        self.status_box.pack(fill="both", expand=True, padx=16, pady=8)
        self._refresh_status()

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(btns, text="Open config folder", command=self._open_config).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Open tmp folder", command=self._open_tmp).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Re-probe paths", command=self._refresh_status).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Cancel", command=self.destroy, fg_color="#444").pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Save", command=self._save).pack(side="right", padx=4)

    def _sync_crf_entry(self) -> None:
        if bool(self.unlock_crf.get()):
            self.crf_entry.configure(state="normal")
        else:
            self.crf_entry.configure(state="normal")
            self.crf_entry.delete(0, "end")
            self.crf_entry.insert(0, str(THESIS_CRF))
            self.crf_entry.configure(state="disabled")

    def _on_unlock(self) -> None:
        if bool(self.unlock_crf.get()):
            ok = messagebox.askokcancel(
                "Unlock CRF",
                "Thesis protocol is CRF 12. Unlocking CRF changes the comparison "
                "and must be recorded in the sidecar / log.\n\nContinue?",
            )
            if not ok:
                self.unlock_crf.set(False)
        self._sync_crf_entry()

    def _browse(self, key: str, is_file: bool) -> None:
        if is_file:
            path = filedialog.askopenfilename(
                parent=self,
                title=f"Select {key}",
                filetypes=[("Executable", "*.exe"), ("All", "*.*")],
            )
        else:
            path = filedialog.askdirectory(parent=self, title=f"Select {key}")
        if path:
            self.entries[key].delete(0, "end")
            self.entries[key].insert(0, str(Path(path)))

    def _draft_cfg(self) -> AppConfig:
        cfg = AppConfig(
            ffmpeg=self.entries["ffmpeg"].get().strip(),
            ffprobe=self.entries["ffprobe"].get().strip(),
            mpv=self.entries["mpv"].get().strip(),
            mpv_portable_config=self.entries["mpv_portable_config"].get().strip(),
            animejanai_mpv=self.entries["animejanai_mpv"].get().strip(),
            shaders_dir=self.entries["shaders_dir"].get().strip(),
            output_dir=self.master_app.cfg.output_dir,
            x265_preset=self.preset.get().strip() or "medium",
            crf=THESIS_CRF,
            crf_unlocked=bool(self.unlock_crf.get()),
            two_parallel_glsl=bool(self.parallel.get()),
            animejanai_engine_note=self.master_app.cfg.animejanai_engine_note,
            live_hwdec=normalize_live_hwdec(self.live_hwdec.get()),
            live_none_force_copy=bool(self.none_force_copy.get()),
        )
        if cfg.crf_unlocked:
            try:
                cfg.crf = int(self.crf_entry.get().strip())
            except ValueError:
                cfg.crf = THESIS_CRF
        return cfg

    def _refresh_status(self) -> None:
        cfg = self._draft_cfg()
        rows = probe_tools(cfg)
        lines = []
        for r in rows:
            flag = "Found" if r.found else "Missing"
            lines.append(f"{r.name:22}  {flag:8}  {r.path}" + (f"  ({r.detail})" if r.detail not in {"Found", "Missing"} else ""))
        gpu = gpu_name() or "not detected"
        lines.append(f"{'GPU':22}  {gpu}")
        x265 = "yes" if ffmpeg_has_libx265(cfg.ffmpeg_path()) else "NO — install Gyan full FFmpeg"
        lines.append(f"{'libx265':22}  {x265}")
        self.status_box.delete("1.0", "end")
        self.status_box.insert("1.0", "\n".join(lines))

    def _open_config(self) -> None:
        os.startfile(appdata_dir())  # noqa: S606 — Windows explorer

    def _open_tmp(self) -> None:
        os.startfile(tmp_dir())  # noqa: S606

    def _save(self) -> None:
        cfg = self._draft_cfg()
        save_config(cfg)
        self.master_app.apply_config(cfg)
        self.destroy()


class DumpLabApp:
    def __init__(self, root, dnd_token) -> None:
        self.root = root
        self.dnd_token = dnd_token
        self.cfg = load_config()
        self.items: dict[str, QueueItem] = {}
        self.running = False
        self.runner: BatchRunner | None = None
        self.cancel_event = threading.Event()
        self.group_a_vars: dict[str, ctk.BooleanVar] = {}
        self.group_b_vars: dict[str, ctk.BooleanVar] = {}
        self.model_helpers: dict[str, ctk.CTkLabel] = {}
        self.model_checks: dict[str, ctk.CTkCheckBox] = {}
        self.live_proc: subprocess.Popen[str] | None = None
        self.live_token: str = ""
        self._live_ani_noted = False
        self._live_launch: LiveLaunch | None = None
        self._live_allow_hwdec_fallback = False
        self._live_restarting = False
        self._live_logged_attached = False
        self._uiq: queue.Queue = queue.Queue()

        root.title(APP_NAME)
        root.geometry("1480x920")
        root.minsize(1180, 740)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self._refresh_tools()
        self._refresh_model_readiness()
        self._refresh_matrix()
        self.root.after(30, self._pump_ui)
        if dnd_token is not None:
            try:
                self.root.drop_target_register(dnd_token)
                self.root.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass

    def call_ui(self, fn) -> None:
        """Marshal a callable onto the Tk thread (Windows Tk is not thread-safe)."""
        if threading.current_thread() is threading.main_thread():
            fn()
        else:
            self._uiq.put(fn)

    def _pump_ui(self) -> None:
        try:
            while True:
                fn = self._uiq.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    try:
                        self.log_box.configure(state="normal")
                        self.log_box.insert("end", f"UI callback error: {exc}\n")
                        self.log_box.configure(state="disabled")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        try:
            if self.root.winfo_exists():
                self.root.after(30, self._pump_ui)
        except Exception:
            pass

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        _style_treeview()
        root = self.root
        root.grid_rowconfigure(1, weight=3)
        root.grid_rowconfigure(4, weight=2)
        root.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            header,
            text=APP_NAME,
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="  fixed 2× dumps  ·  FSRCNNX / Anime4K / AnimeJaNai  ·  not RTX Video Super Resolution",
            text_color="#8a8a8a",
        ).pack(side="left", padx=8)
        ctk.CTkButton(header, text="⚙  Settings", width=120, command=self._open_settings).pack(
            side="right"
        )

        body = ctk.CTkFrame(root, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self._build_queue(body)
        self._build_models(body)
        self._build_output_bar(root)
        self._build_progress(root)
        self._build_log(root)
        self._build_statusbar(root)

    def _build_queue(self, parent) -> None:
        card = ctk.CTkFrame(parent)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        card.grid_rowconfigure(2, weight=1)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="Queue", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        tools = ctk.CTkFrame(card, fg_color="transparent")
        tools.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        for text, cmd in (
            ("Add files", self._add_files),
            ("Add folder", self._add_folder),
            ("Remove", self._remove_selected),
            ("Clear", self._clear_queue),
        ):
            ctk.CTkButton(tools, text=text, width=100, command=cmd).pack(side="left", padx=3)

        ctk.CTkButton(tools, text="Live", width=70, command=lambda: self._set_content("Live")).pack(
            side="left", padx=(16, 3)
        )
        ctk.CTkButton(
            tools, text="Animation", width=90, command=lambda: self._set_content("Animation")
        ).pack(side="left", padx=3)

        ctk.CTkLabel(tools, text="Force 2×:").pack(side="left", padx=(16, 4))
        self.force_combo = ctk.CTkComboBox(
            tools, values=list(FORCE_VALUES), width=120, command=self._apply_force
        )
        self.force_combo.set(FORCE_VALUES[0])
        self.force_combo.pack(side="left")
        ctk.CTkButton(tools, text="Apply force", width=100, command=self._apply_force_btn).pack(
            side="left", padx=4
        )

        tree_wrap = tk.Frame(card, bg="#1a1a1a")
        tree_wrap.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 10))
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            tree_wrap,
            columns=COLS,
            show="headings",
            selectmode="extended",
            style="Dump.Treeview",
        )
        for c in COLS:
            self.tree.heading(c, text=COL_HEAD[c])
            self.tree.column(c, width=COL_W[c], anchor="w", stretch=(c in {"file", "status"}))
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview, style="Dump.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("unsupported", foreground="#ff6b6b")
        self.tree.tag_configure("ok", foreground="#69db7c")
        self.tree.tag_configure("failed", foreground="#ff6b6b")
        self.tree.tag_configure("running", foreground="#74c0fc")
        self.tree.tag_configure("exists", foreground="#adb5bd")
        self.tree.tag_configure("queued", foreground="#e6e6e6")
        self.tree.tag_configure("warn", foreground="#ffd43b")
        self.tree.bind("<Delete>", lambda _e: self._remove_selected())

        hint = ctk.CTkLabel(
            card,
            text="Drop files/folders here. Content tag is visual + summary only — it does not enable Group B.",
            text_color="#8a8a8a",
            font=ctk.CTkFont(size=12),
        )
        hint.grid(row=3, column=0, sticky="w", padx=12, pady=(0, 8))

        if self.dnd_token is not None:
            try:
                self.tree.drop_target_register(self.dnd_token)
                self.tree.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass

    def _build_models(self, parent) -> None:
        col = ctk.CTkScrollableFrame(parent, label_text="Models")
        col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # Card A
        a = ctk.CTkFrame(col, border_width=1, border_color="#3a3a3a")
        a.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            a,
            text="A — Standard  (live action + animation)",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            a,
            text="Default ON. Always run on selected files when checked, including Animation-tagged files.",
            text_color="#8a8a8a",
            wraplength=420,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 8))
        for spec in GROUP_A:
            self._model_row(a, spec, self.group_a_vars)

        # Card B
        b = ctk.CTkFrame(col, border_width=1, border_color="#3a3a3a")
        b.pack(fill="x")
        ctk.CTkLabel(
            b,
            text="B — Anime / drawing  (extra)",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(10, 2))
        self.group_b_master = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            b,
            text="Also generate anime / drawing models",
            variable=self.group_b_master,
            command=self._on_group_b_master,
        ).pack(anchor="w", padx=12, pady=6)
        ctk.CTkLabel(
            b,
            text="When OFF, Group B checkboxes are ignored. When ON, Group A still runs unless unchecked. "
            "Animation outputs then get standard + anime files.",
            text_color="#8a8a8a",
            wraplength=420,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 8))
        for spec in GROUP_B:
            self._model_row(b, spec, self.group_b_vars)

        self.ani_note = ctk.CTkLabel(
            b,
            text=ENGINE_NOTE,
            text_color="#c9a227",
            wraplength=420,
            justify="left",
        )
        self.ani_note.pack(anchor="w", padx=12, pady=(4, 12))
        if self.cfg.animejanai_engine_note:
            self.ani_note.configure(
                text="TensorRT engine builds once and can take several minutes. "
                "This host has already launched AnimeJaNai at least once this install."
            )

    def _model_row(self, parent, spec: ModelSpec, store: dict[str, ctk.BooleanVar]) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=10, pady=4)
        var = ctk.BooleanVar(value=spec.default_enabled)
        store[spec.token] = var
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x")
        chk = ctk.CTkCheckBox(
            top,
            text=spec.ui_label,
            variable=var,
            command=self._refresh_matrix,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        chk.pack(side="left", anchor="w")
        ctk.CTkButton(
            top,
            text="Play",
            width=56,
            height=24,
            fg_color="#333333",
            hover_color="#444444",
            command=lambda t=spec.token: self._play_live_from_row(t),
        ).pack(side="right", padx=4)
        self.model_checks[spec.token] = chk
        meta = ctk.CTkLabel(
            frame,
            text=f"{spec.token}  ·  {spec.device_label}  ·  {spec.content}",
            text_color="#a0a0a0",
            font=ctk.CTkFont(size=12),
            wraplength=420,
            justify="left",
        )
        meta.pack(anchor="w", padx=24)
        helper = ctk.CTkLabel(
            frame,
            text=spec.helper,
            text_color="#6c6c6c",
            font=ctk.CTkFont(size=12),
            wraplength=420,
            justify="left",
        )
        helper.pack(anchor="w", padx=24, pady=(0, 4))
        self.model_helpers[spec.token] = helper

    def _build_output_bar(self, root) -> None:
        bar = ctk.CTkFrame(root)
        bar.grid(row=2, column=0, sticky="ew", padx=12, pady=4)
        bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(bar, text="Output folder").grid(row=0, column=0, padx=(12, 6), pady=8)
        self.out_entry = ctk.CTkEntry(bar)
        self.out_entry.insert(0, self.cfg.output_dir)
        self.out_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=8)
        ctk.CTkButton(bar, text="Browse", width=90, command=self._browse_output).grid(
            row=0, column=2, padx=4
        )

        self.overwrite = ctk.BooleanVar(value=False)
        self.dry_run = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="Overwrite existing", variable=self.overwrite).grid(
            row=0, column=3, padx=8
        )
        ctk.CTkCheckBox(bar, text="Dry-run", variable=self.dry_run).grid(row=0, column=4, padx=8)

        self.matrix = ctk.CTkLabel(
            bar,
            text="",
            justify="left",
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.matrix.grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 8))

        btns = ctk.CTkFrame(bar, fg_color="transparent")
        btns.grid(row=1, column=3, columnspan=2, sticky="e", padx=8, pady=(0, 8))
        self.start_btn = ctk.CTkButton(
            btns,
            text="Start dumps",
            width=160,
            height=40,
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._start,
        )
        self.start_btn.pack(side="left", padx=4)
        self.cancel_btn = ctk.CTkButton(
            btns,
            text="Cancel",
            width=100,
            height=40,
            fg_color="#8a1f1f",
            hover_color="#a61e1e",
            command=self._cancel,
            state="disabled",
        )
        self.cancel_btn.pack(side="left", padx=4)

        live_row = ctk.CTkFrame(bar, fg_color="transparent")
        live_row.grid(row=2, column=0, columnspan=5, sticky="ew", padx=12, pady=(0, 2))
        ctk.CTkLabel(live_row, text="Live model").pack(side="left", padx=(0, 8))
        labels = _live_model_labels()
        self.live_combo = ctk.CTkComboBox(live_row, values=labels, width=460)
        self.live_combo.set(labels[0] if labels else "")
        self.live_combo.pack(side="left", padx=4)
        self.play_live_btn = ctk.CTkButton(
            live_row,
            text="▶ Play live",
            width=140,
            height=36,
            command=self._play_live,
        )
        self.play_live_btn.pack(side="left", padx=4)
        self.stop_live_btn = ctk.CTkButton(
            live_row,
            text="Stop live",
            width=110,
            height=36,
            fg_color="#444444",
            hover_color="#555555",
            command=self._stop_live,
            state="disabled",
        )
        self.stop_live_btn.pack(side="left", padx=4)
        ctk.CTkLabel(
            bar,
            text="None: nvdec (no copy, Chrome-like). AI live: nvdec-copy (shaders need the copy). Dumps: --hwdec=no.",
            text_color="#8a8a8a",
            font=ctk.CTkFont(size=12),
        ).grid(row=3, column=0, columnspan=5, sticky="w", padx=12, pady=(0, 8))

    def _build_progress(self, root) -> None:
        wrap = ctk.CTkFrame(root, fg_color="transparent")
        wrap.grid(row=3, column=0, sticky="ew", padx=16, pady=2)
        wrap.grid_columnconfigure(0, weight=1)
        self.prog_label = ctk.CTkLabel(wrap, text="Idle", anchor="w")
        self.prog_label.grid(row=0, column=0, sticky="w")
        self.prog = ctk.CTkProgressBar(wrap)
        self.prog.grid(row=1, column=0, sticky="ew", pady=4)
        self.prog.set(0)

    def _build_log(self, root) -> None:
        wrap = ctk.CTkFrame(root)
        wrap.grid(row=4, column=0, sticky="nsew", padx=12, pady=4)
        wrap.grid_rowconfigure(1, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(wrap, text="Log", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 0)
        )
        self.log_box = ctk.CTkTextbox(wrap, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.log_box.configure(state="disabled")

    def _build_statusbar(self, root) -> None:
        bar = ctk.CTkFrame(root, height=28, fg_color="#1a1a1a")
        bar.grid(row=5, column=0, sticky="ew")
        self.status_ffmpeg = ctk.CTkLabel(bar, text="ffmpeg —", text_color="#8a8a8a")
        self.status_mpv = ctk.CTkLabel(bar, text="mpv —", text_color="#8a8a8a")
        self.status_ani = ctk.CTkLabel(bar, text="AnimeJaNai —", text_color="#8a8a8a")
        self.status_gpu = ctk.CTkLabel(bar, text="GPU —", text_color="#8a8a8a")
        self.status_ffmpeg.pack(side="left", padx=14, pady=4)
        self.status_mpv.pack(side="left", padx=14)
        self.status_ani.pack(side="left", padx=14)
        self.status_gpu.pack(side="left", padx=14)

    # ------------------------------------------------------------------ helpers
    def log(self, msg: str) -> None:
        def _() -> None:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.call_ui(_)

    def apply_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.out_entry.delete(0, "end")
        self.out_entry.insert(0, cfg.output_dir)
        self._refresh_tools()
        self._refresh_model_readiness()
        self._refresh_matrix()

    def _open_settings(self) -> None:
        self._persist_output()
        SettingsDialog(self)

    def _persist_output(self) -> None:
        self.cfg.output_dir = self.out_entry.get().strip() or self.cfg.output_dir
        save_config(self.cfg)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Output folder", initialdir=self.out_entry.get() or None)
        if path:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, path)
            self._persist_output()

    def _on_group_b_master(self) -> None:
        on = bool(self.group_b_master.get())
        for spec in GROUP_B:
            chk = self.model_checks.get(spec.token)
            if chk is not None:
                chk.configure(text_color=("#e6e6e6" if on else "#6c6c6c"))
        self._refresh_matrix()

    def _current_models(self) -> list[ModelSpec]:
        a = {t: bool(v.get()) for t, v in self.group_a_vars.items()}
        b = {t: bool(v.get()) for t, v in self.group_b_vars.items()}
        return selected_models(a, bool(self.group_b_master.get()), b)

    def _queue_list(self) -> list[QueueItem]:
        return list(self.items.values())

    def _refresh_matrix(self) -> None:
        text = job_matrix_text(self._queue_list(), self._current_models())
        self.matrix.configure(text=text)

    def _refresh_tools(self) -> None:
        rows = {r.name: r for r in probe_tools(self.cfg)}

        def paint(label: ctk.CTkLabel, key: str, pretty: str) -> None:
            r = rows.get(key)
            if r is None:
                label.configure(text=f"{pretty} ?", text_color="#8a8a8a")
                return
            if r.found:
                label.configure(text=f"{pretty} ok", text_color="#69db7c")
            else:
                label.configure(text=f"{pretty} missing", text_color="#ff6b6b")

        paint(self.status_ffmpeg, "ffmpeg", "ffmpeg")
        paint(self.status_mpv, "mpv", "mpv")
        paint(self.status_ani, "AnimeJaNai", "AnimeJaNai")
        gpu = gpu_name()
        if gpu:
            self.status_gpu.configure(text=gpu, text_color="#74c0fc")
        else:
            self.status_gpu.configure(text="GPU not detected", text_color="#8a8a8a")

        ff = rows.get("ffmpeg")
        if ff and ff.found and not ffmpeg_has_libx265(self.cfg.ffmpeg_path()):
            self.status_ffmpeg.configure(text="ffmpeg (no libx265)", text_color="#ffd43b")

    def _refresh_model_readiness(self) -> None:
        from app.backend_mpv import model_ready_glsl
        from app.backend_animejanai import model_ready_animejanai

        for spec in list(GROUP_A) + list(GROUP_B):
            helper = self.model_helpers.get(spec.token)
            chk = self.model_checks.get(spec.token)
            err: str | None
            if spec.backend == "mpv_glsl":
                err = model_ready_glsl(spec, self.cfg.mpv_path(), self.cfg.shaders_path())
            else:
                err = model_ready_animejanai(self.cfg.animejanai_path())
            if err:
                if helper:
                    helper.configure(text=f"Missing — {err}", text_color="#ff6b6b")
                if chk:
                    chk.configure(state="normal")  # still checkable; Start will refuse
            else:
                if helper:
                    helper.configure(text=spec.helper, text_color="#6c6c6c")

    # ------------------------------------------------------------------ queue
    def _on_drop(self, event) -> None:
        self._ingest_paths(_parse_drop_data(getattr(event, "data", "") or ""))

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Add videos",
            filetypes=[
                ("Video", " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))),
                ("All", "*.*"),
            ],
        )
        self._ingest_paths([Path(p) for p in paths])

    def _add_folder(self) -> None:
        path = filedialog.askdirectory(title="Add folder of videos")
        if path:
            self._ingest_paths([Path(path)])

    def _ingest_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        videos = collect_videos(paths)
        if not videos:
            messagebox.showinfo(APP_NAME, "No video files found.")
            return
        for p in videos:
            iid = str(p.resolve()) if p.exists() else str(p)
            if iid in self.items:
                continue
            item = QueueItem(
                path=p,
                width=0,
                height=0,
                fps=0.0,
                fps_str="",
                duration=0.0,
                has_audio=False,
                height_class="…",
                target_w=None,
                target_h=None,
                status="probing",
                iid=iid,
            )
            self.items[iid] = item
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(p.name, "…", "…", "…", "…", "Live", "probing"),
                tags=("queued",),
            )
        self._refresh_matrix()
        threading.Thread(target=self._probe_worker, args=(list(videos),), daemon=True).start()

    def _probe_worker(self, paths: list[Path]) -> None:
        for p in paths:
            try:
                iid = str(p.resolve()) if p.exists() else str(p)
                result = probe_file(self.cfg.ffprobe_path(), p)
                self.call_ui(lambda i=iid, r=result: self._apply_probe(i, r))
            except Exception as exc:
                self.call_ui(lambda m=f"probe error {p}: {exc}": self.log(m))

    def _apply_probe(self, iid: str, result) -> None:
        item = self.items.get(iid)
        if item is None:
            return
        if result.error:
            item.status = result.error
            item.reason = result.error
            self._paint_row(iid, item, tag="failed")
            self.log(f"probe failed: {item.path.name}: {result.error}")
            self._refresh_matrix()
            return
        item.width = result.width
        item.height = result.height
        item.fps = result.fps
        item.fps_str = result.fps_str
        item.duration = result.duration
        item.has_audio = result.has_audio
        plan = classify_height(item.width, item.height, item.force_target or None)
        item.height_class = plan.height_class
        item.target_w = plan.target_w
        item.target_h = plan.target_h
        item.reason = plan.reason
        item.fps_warning = not fps_is_standard(item.fps)
        if plan.height_class == CLASS_UNSUPPORTED:
            item.status = plan.reason
            tag = "unsupported"
        else:
            item.status = "queued"
            tag = "warn" if item.fps_warning else "queued"
            if item.fps_warning:
                self.log(
                    f"fps warning {item.path.name}: {format_fps(item.fps, item.fps_str)} "
                    f"(not 24/25/30/50/60 — still allowed)"
                )
        self._paint_row(iid, item, tag=tag)
        self._refresh_matrix()

    def _paint_row(self, iid: str, item: QueueItem, tag: str) -> None:
        if not self.tree.exists(iid):
            return
        cls = item.height_class
        if item.force_target:
            cls = f"{item.force_target} (forced)"
        wxh = f"{item.width}x{item.height}" if item.width else "—"
        self.tree.item(
            iid,
            values=(
                item.path.name,
                wxh,
                format_fps(item.fps, item.fps_str),
                format_duration(item.duration),
                cls,
                item.content_tag,
                item.status,
            ),
            tags=(tag,),
        )

    def _selected_iids(self) -> list[str]:
        return list(self.tree.selection())

    def _remove_selected(self) -> None:
        for iid in self._selected_iids():
            self.tree.delete(iid)
            self.items.pop(iid, None)
        self._refresh_matrix()

    def _clear_queue(self) -> None:
        for iid in list(self.items):
            if self.tree.exists(iid):
                self.tree.delete(iid)
        self.items.clear()
        self._refresh_matrix()

    def _set_content(self, tag: str) -> None:
        for iid in self._selected_iids():
            item = self.items.get(iid)
            if item is None:
                continue
            item.content_tag = tag
            tags = self.tree.item(iid, "tags") or ("queued",)
            self._paint_row(iid, item, tag=tags[0] if tags else "queued")

    def _apply_force(self, _value: str | None = None) -> None:
        # Combo command fires on select; apply to current selection.
        self._apply_force_btn()

    def _apply_force_btn(self) -> None:
        choice = self.force_combo.get()
        force = ""
        if choice.startswith("720"):
            force = "720p"
        elif choice.startswith("2160"):
            force = "2160p"
        for iid in self._selected_iids():
            item = self.items.get(iid)
            if item is None or item.width <= 0:
                continue
            item.force_target = force
            plan = classify_height(item.width, item.height, force or None)
            item.height_class = plan.height_class
            item.target_w = plan.target_w
            item.target_h = plan.target_h
            item.reason = plan.reason
            if plan.height_class == CLASS_UNSUPPORTED:
                item.status = plan.reason
                tag = "unsupported"
            else:
                item.status = "queued"
                tag = "queued"
            self._paint_row(iid, item, tag=tag)
        self._refresh_matrix()

    # ------------------------------------------------------------------ run
    def _start(self) -> None:
        if self.running:
            return
        if self._live_is_running():
            if not messagebox.askokcancel(
                APP_NAME,
                "Live mpv is still open. Dumps already need the GPU.\n\n"
                "Stop live and start dumps?",
            ):
                return
            self._stop_live()
        self._persist_output()
        items = self._queue_list()
        models = self._current_models()
        if not items:
            messagebox.showerror(APP_NAME, "Queue is empty. Add one or more videos.")
            return
        if not models:
            messagebox.showerror(
                APP_NAME,
                "No model checked. Enable Group A boxes and/or turn on Group B.",
            )
            return
        eligible = [i for i in items if i.target_w and i.target_h]
        if not eligible:
            messagebox.showerror(
                APP_NAME,
                "No eligible files. 2× only: 360p-class (height 300–400) or "
                "1080p-class (height 1000–1200). Use Force 2× target for odd sizes.",
            )
            return
        if not self.cfg.ffmpeg_path().is_file():
            messagebox.showerror(APP_NAME, f"ffmpeg missing: {self.cfg.ffmpeg_path()}")
            return
        if not self.cfg.ffprobe_path().is_file():
            messagebox.showerror(APP_NAME, f"ffprobe missing: {self.cfg.ffprobe_path()}")
            return

        missing = validate_models_ready(models, self.cfg)
        if missing:
            messagebox.showerror(
                APP_NAME,
                "Cannot start — selected model(s) are missing a binary or shader:\n\n"
                + "\n".join(missing),
            )
            return

        out_dir = Path(self.out_entry.get().strip())
        if not out_dir.drive and not str(out_dir):
            messagebox.showerror(APP_NAME, "Output folder is empty.")
            return
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Cannot create output folder:\n{out_dir}\n{exc}")
            return

        overwrite = bool(self.overwrite.get())
        dry = bool(self.dry_run.get())
        try:
            jobs = expand_jobs(eligible, models, out_dir, overwrite)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        if not jobs:
            messagebox.showerror(APP_NAME, "Nothing to do.")
            return

        fps_warned = [i for i in eligible if i.fps_warning]
        if fps_warned:
            names = ", ".join(i.path.name for i in fps_warned[:8])
            extra = "…" if len(fps_warned) > 8 else ""
            if not messagebox.askokcancel(
                APP_NAME,
                f"{len(fps_warned)} file(s) are not 24/25/30/50/60 fps ({names}{extra}).\n"
                "Still allowed — continue?",
            ):
                return

        self.log("—" * 60)
        self.log(
            f"Starting {len(jobs)} dumps  ·  CRF {self.cfg.effective_crf()}  "
            f"preset {self.cfg.x265_preset}  ·  {'DRY-RUN' if dry else 'encode'}"
        )
        self.log(job_matrix_text(eligible, models))
        self.running = True
        self.cancel_event = threading.Event()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self._sync_live_buttons()
        self.prog.set(0)
        self.prog_label.configure(text="Starting…")

        def on_ani() -> None:
            if not self.cfg.animejanai_engine_note:
                self.cfg.animejanai_engine_note = True
                save_config(self.cfg)
                self.call_ui(
                    lambda: self.ani_note.configure(
                        text="TensorRT engine builds once and can take several minutes. "
                        "This host has already launched AnimeJaNai at least once this install."
                    )
                )

        runner = BatchRunner(
            self.cfg,
            dry_run=dry,
            parallel_glsl=bool(self.cfg.two_parallel_glsl),
            log=self.log,
            progress=self._on_progress,
            job_status=self._on_job_status,
            cancel_event=self.cancel_event,
            on_animejanai_started=on_ani,
        )
        self.runner = runner
        threading.Thread(target=self._run_thread, args=(runner, jobs, out_dir), daemon=True).start()

    def _run_thread(self, runner: BatchRunner, jobs: list[DumpJob], out_dir: Path) -> None:
        try:
            results = runner.run(jobs)
            try:
                path = write_manifest(out_dir, results_to_manifest(results))
                self.log(f"manifest: {path}")
            except Exception as exc:
                self.log(f"manifest write failed: {exc}")
            ok = sum(1 for r in results if r.status == "ok")
            failed = sum(1 for r in results if r.status == "failed")
            skipped = sum(1 for r in results if r.status in {"exists", "dry-run", "cancelled"})
            self.log(
                f"Batch done. ok={ok} failed={failed} skipped/other={skipped} total={len(results)}"
            )
        except Exception:
            self.log(traceback.format_exc())
        finally:
            self.call_ui(self._run_finished)

    def _run_finished(self) -> None:
        self.running = False
        self.runner = None
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self._sync_live_buttons()
        if not self._live_is_running():
            self.prog_label.configure(text="Idle")

    def _cancel(self) -> None:
        if not self.running:
            return
        self.log("Cancel requested — killing current mpv/ffmpeg process tree")
        self.cancel_event.set()
        if self.runner:
            self.runner.cancel()

    def _on_progress(
        self, index: int, total: int, token: str, detail: str, pct: float | None
    ) -> None:
        def _() -> None:
            self.prog_label.configure(
                text=f"File {index}/{total}  ·  {token}  ·  {detail}"
            )
            if pct is not None:
                self.prog.set(max(0.0, min(1.0, pct / 100.0)))
            else:
                # job index as coarse bar
                self.prog.set(max(0.0, min(1.0, (index - 1) / max(total, 1))))

        self.call_ui(_)

    def _on_job_status(self, key: str, status: str, error: str) -> None:
        def _() -> None:
            if "::" not in key:
                return
            path_s, token = key.rsplit("::", 1)
            iid = path_s
            item = self.items.get(iid)
            if item is None:
                for k, v in self.items.items():
                    if v.iid == path_s or str(v.path) == path_s or k == path_s:
                        item = v
                        iid = k
                        break
            if item is None:
                return
            item.status = f"{token}: {status}" + (f" ({error[:80]})" if error and status == "failed" else "")
            tag = {
                "ok": "ok",
                "failed": "failed",
                "running": "running",
                "exists": "exists",
                "dry-run": "queued",
                "cancelled": "failed",
            }.get(status, "queued")
            self._paint_row(iid, item, tag=tag)

        self.call_ui(_)

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askokcancel(APP_NAME, "A dump is running. Cancel and quit?"):
                return
            self._cancel()
        if self._live_is_running():
            self._stop_live()
        self._persist_output()
        self.root.destroy()

    # ------------------------------------------------------------------ live
    def _live_is_passthrough(self, val: str | None = None) -> bool:
        raw = (val if val is not None else self.live_combo.get() or "").strip()
        return (
            raw.endswith(f"[{TOKEN_LIVE_NONE}]")
            or raw == TOKEN_LIVE_NONE
            or raw.startswith(LIVE_NONE_LABEL)
        )

    def _live_spec_from_combo(self) -> ModelSpec | None:
        val = (self.live_combo.get() or "").strip()
        if self._live_is_passthrough(val):
            return None
        for spec in MODELS:
            if val.endswith(f"[{spec.token}]") or val == spec.token:
                return spec
        return MODELS_BY_TOKEN.get(val)

    def _set_live_combo_token(self, token: str) -> None:
        if token == TOKEN_LIVE_NONE:
            self.live_combo.set(_live_none_label())
            return
        for label in _live_model_labels():
            if label.endswith(f"[{token}]"):
                self.live_combo.set(label)
                return

    def _live_is_running(self) -> bool:
        proc = self.live_proc
        return proc is not None and proc.poll() is None

    def _sync_live_buttons(self) -> None:
        playing = self._live_is_running()
        if getattr(self, "stop_live_btn", None) is not None:
            self.stop_live_btn.configure(state="normal" if playing else "disabled")
        if getattr(self, "play_live_btn", None) is not None:
            self.play_live_btn.configure(
                state="disabled" if self.running else "normal"
            )

    def _playable_item(self, item: QueueItem | None, *, passthrough: bool = False) -> bool:
        if item is None:
            return False
        if item.width <= 0 or item.status == "probing":
            return False
        if passthrough:
            return True
        if item.height_class == CLASS_UNSUPPORTED:
            return False
        if not item.target_w or not item.target_h:
            return False
        return True

    def _live_source_item(self, *, passthrough: bool = False) -> QueueItem | None:
        iids = self._selected_iids()
        playable: list[QueueItem] = []
        for iid in iids:
            item = self.items.get(iid)
            if item is not None and self._playable_item(item, passthrough=passthrough):
                playable.append(item)
        if not playable:
            return None
        if len(iids) > 1:
            self.log(f"live: several files selected — using {playable[0].path.name}")
        return playable[0]

    def _play_live_from_row(self, token: str) -> None:
        self._set_live_combo_token(token)
        self._play_live()

    def _play_live(self) -> None:
        if self.running:
            messagebox.showerror(
                APP_NAME,
                "A dump batch is running. Dumps already own the GPU.",
            )
            return
        iids = self._selected_iids()
        if not iids:
            messagebox.showerror(
                APP_NAME,
                "Select one queue file to play live (not unsupported).",
            )
            return
        passthrough = self._live_is_passthrough()
        spec = self._live_spec_from_combo()
        if spec is None and not passthrough:
            messagebox.showerror(APP_NAME, "Pick a Live model in the combobox.")
            return
        item = self._live_source_item(passthrough=passthrough)
        if item is None:
            first = self.items.get(iids[0])
            if first is not None and first.status == "probing":
                messagebox.showerror(
                    APP_NAME,
                    "File is still probing. Wait for WxH before Play live.",
                )
                return
            if first is not None and first.width <= 0:
                messagebox.showerror(
                    APP_NAME,
                    f"Cannot play live: {first.status or 'no WxH yet'}.",
                )
                return
            messagebox.showerror(
                APP_NAME,
                "Selected file is unsupported for 2× "
                "(360p or 1080p, or Force 2× target). "
                "Pick None — native (no AI, hw decode) to play the source anyway.",
            )
            return
        err = model_ready_live(
            spec,
            self.cfg.mpv_path(),
            self.cfg.shaders_path(),
            self.cfg.animejanai_path(),
        )
        if err:
            messagebox.showerror(APP_NAME, err)
            return
        if self._live_is_running():
            self.log("live: stopping previous mpv")
            self._stop_live()
        try:
            launch = prepare_live(
                spec,
                item.path,
                int(item.target_w or 0),
                int(item.target_h or 0),
                mpv=self.cfg.mpv_path(),
                shaders_dir=self.cfg.shaders_path(),
                animejanai_configured=self.cfg.animejanai_path(),
                hwdec=normalize_live_hwdec(self.cfg.live_hwdec),
                none_force_copy=bool(self.cfg.live_none_force_copy),
            )
        except LiveError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Cannot start live: {exc}")
            return

        for note in launch.notes:
            self.log(note)
        if launch.engine_note and not self._live_ani_noted:
            self.log(ENGINE_NOTE)
            self._live_ani_noted = True
            if not self.cfg.animejanai_engine_note:
                self.cfg.animejanai_engine_note = True
                save_config(self.cfg)
                self.ani_note.configure(
                    text="TensorRT engine builds once and can take several minutes. "
                    "This host has already launched AnimeJaNai at least once this install."
                )
        self.log(launch.hwdec_log_line())
        self.log(f"mpv live: {format_cmd(launch.cmd)}")

        try:
            proc = start_live_process(
                launch.cmd, cwd=launch.cwd, on_line=self._on_live_mpv_line
            )
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Failed to launch mpv: {exc}")
            return
        self.live_proc = proc
        self.live_token = launch.token
        self._live_launch = launch
        self._live_allow_hwdec_fallback = bool(launch.hwdec_fallbacks)
        self._live_logged_attached = False
        self._live_restarting = False
        msg = (
            f"live hwdec={launch.hwdec} · playing {launch.token} — "
            "close the mpv window or click Stop live"
        )
        self.log(f"live playing {launch.token} — close the mpv window or click Stop live")
        self.prog_label.configure(text=msg)
        self._sync_live_buttons()
        self.root.after(400, self._poll_live)

    def _on_live_mpv_line(self, line: str) -> None:
        if self._live_allow_hwdec_fallback and is_hwdec_init_failure(line):
            self.call_ui(lambda err=line: self._fallback_live_hwdec(err))
            return
        attached = parse_attached_hwdec(line)
        if attached is not None:
            self.call_ui(lambda a=attached: self._log_attached_hwdec(a))
        low = line.lower()
        if any(
            k in low
            for k in (
                "error",
                "fatal",
                "failed",
                "cannot",
                "no such",
                "vf_animejanai",
                "tensorrt",
                "engine",
            )
        ):
            self.log(f"  mpv live: {line}")

    def _log_attached_hwdec(self, attached: str) -> None:
        if self._live_logged_attached:
            return
        self._live_logged_attached = True
        self.log(f"live attached hwdec={attached}")

    def _fallback_live_hwdec(self, error_line: str) -> None:
        if not self._live_allow_hwdec_fallback:
            return
        launch = self._live_launch
        if launch is None or not launch.hwdec_fallbacks:
            self._live_allow_hwdec_fallback = False
            return
        failed = launch.hwdec
        next_hwdec = launch.hwdec_fallbacks.pop(0)
        self._live_allow_hwdec_fallback = False
        self._live_restarting = True
        self.log(f"live: {failed} failed to init: {error_line}")
        self.log(f"live: restarting once with hwdec={next_hwdec}")
        proc = self.live_proc
        self.live_proc = None
        if proc is not None and proc.poll() is None:
            kill_tree(proc)
        launch.cmd = cmd_with_hwdec(launch.cmd, next_hwdec)
        launch.hwdec = next_hwdec
        self.log(launch.hwdec_log_line())
        self.log(f"mpv live: {format_cmd(launch.cmd)}")
        try:
            new_proc = start_live_process(
                launch.cmd, cwd=launch.cwd, on_line=self._on_live_mpv_line
            )
        except OSError as exc:
            self._live_restarting = False
            self._live_launch = None
            self.live_token = ""
            self._sync_live_buttons()
            if not self.running:
                self.prog_label.configure(text="Idle")
            messagebox.showerror(
                APP_NAME, f"Failed to relaunch mpv with hwdec={next_hwdec}: {exc}"
            )
            return
        self.live_proc = new_proc
        self._live_logged_attached = False
        self._live_allow_hwdec_fallback = bool(launch.hwdec_fallbacks)
        self._live_restarting = False
        msg = (
            f"live hwdec={next_hwdec} · playing {launch.token} — "
            "close the mpv window or click Stop live"
        )
        self.prog_label.configure(text=msg)
        self._sync_live_buttons()
        self.root.after(400, self._poll_live)

    def _poll_live(self) -> None:
        if self._live_restarting:
            try:
                if self.root.winfo_exists():
                    self.root.after(400, self._poll_live)
            except Exception:
                pass
            return
        proc = self.live_proc
        if proc is None:
            return
        if proc.poll() is not None:
            token = self.live_token
            self.live_proc = None
            self.live_token = ""
            self._live_launch = None
            self._live_allow_hwdec_fallback = False
            self._live_logged_attached = False
            self.log(f"live: mpv window closed" + (f" ({token})" if token else ""))
            self._sync_live_buttons()
            if not self.running:
                self.prog_label.configure(text="Idle")
            return
        try:
            if self.root.winfo_exists():
                self.root.after(400, self._poll_live)
        except Exception:
            pass

    def _stop_live(self) -> None:
        proc = self.live_proc
        token = self.live_token
        self.live_proc = None
        self.live_token = ""
        self._live_launch = None
        self._live_allow_hwdec_fallback = False
        self._live_logged_attached = False
        self._live_restarting = False
        if proc is not None and proc.poll() is None:
            kill_tree(proc)
            self.log("live: stopped" + (f" ({token})" if token else ""))
        self._sync_live_buttons()
        if not self.running:
            self.prog_label.configure(text="Idle")


def run_app() -> None:
    root, dnd_token = _create_root()
    DumpLabApp(root, dnd_token)
    root.mainloop()
