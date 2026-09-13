# ClientSR Dump Lab

Windows desktop app that batch-upscales videos at a **fixed 2×** using the real-time client models from a bachelor thesis comparison, then writes **MP4** files whose names encode **which model** and **which device profile** they represent.

This is **not** NVIDIA RTX Video Super Resolution. Do not name the app, window, folder, or config key “VSR”.

Window title: **ClientSR Dump Lab**  
Config / temp: `%LOCALAPPDATA%\ClientSRDumpLab\`

## Install (uv)

Python **3.11** or **3.12**. This repo is set up for **uv**.

```powershell
cd clientsr-dump-lab   # this repo; or copy it to C:\VSR\clientsr-dump-lab if you want it next to FFmpeg

uv python install 3.12
uv sync
uv run python -m app
```

After `uv sync`, double-click `launch.bat` in this folder (or run it from a prompt). It uses `.venv\Scripts\pythonw.exe` so no extra console stays open.

pip equivalent (if you are not using uv):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app
```

FFmpeg is **not** bundled. Use the Gyan.dev **full** build already on this machine. Do not download another FFmpeg.

## Path defaults

All paths are editable in **Settings** (gear) and persisted in `%LOCALAPPDATA%\ClientSRDumpLab\config.yaml`.

| Tool | Default path |
|---|---|
| ffmpeg | `C:\VSR\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe` |
| ffprobe | `C:\VSR\ffmpeg-9.0.1-full_build\bin\ffprobe.exe` |
| Plain mpv (GLSL: FSRCNNX + Anime4K) | `C:\Tools\mpv\mpv.exe` |
| Plain mpv portable_config | `C:\Tools\mpv\portable_config\` |
| AnimeJaNai mpv | `C:\Tools\mpv-AnimeJaNai\mpv.exe` (fallback `mpvnet.exe` in the same folder) |
| Shaders directory | `C:\Tools\mpv\portable_config\shaders\` |

Temp intermediates: `%LOCALAPPDATA%\ClientSRDumpLab\tmp\`  
Default output: `W:\dumps\` if the W: drive exists, else `%USERPROFILE%\Videos\ClientSRDump\`.

At startup the app probes each path and shows **Found / Missing**. Missing tools do not prevent the window from opening; only the models that need the missing tool are refused at Start.

This dump host is an **NVIDIA RTX 4080 Super (16 GB)**. Laptop-class shaders (MacBook Air M4 and Surface Laptop 4) are still rendered here; both laptops play the same dumped file.

## Scale policy (hard rule)

Only **2×**. After ffprobe:

| Source height | Class | Target |
|---|---|---|
| 300–400 (360p class, e.g. 640×360) | `360p→720p` | source × 2 (typically 1280×720) |
| 1000–1200 (1080p class, e.g. 1920×1080) | `1080p→4K` | source × 2 (typically 3840×2160) |
| Anything else (already 720p, 1440p, 4K, …) | `unsupported` | **do not upscale** |

Unsupported files are marked red with reason `unsupported source height (2× only: 360p or 1080p)`. Per-file dropdown **Force 2× target: 720p | 2160p** overrides odd sizes (720p → 1280×720, 2160p → 3840×2160). Default off.

Never ask a model to do 1.5× or 4× as a native factor. AnimeJaNai and FSRCNNX are native **2×** networks. Anime4K CNN passes are 2×; mpv output size is locked to the 2× target so AutoDownscale does not change the comparison.

Optional **Bilinear to 4K when not 4K** (default **OFF**, main bar + Settings, persisted as `bilinear_to_4k`). Models still dump at native 2×. If that 2× canvas is not already 3840×2160 (typically `360p→720p` → 1280×720), the FFmpeg conform step adds `-vf scale=3840:2160:flags=bilinear`. Already-4K 2× dumps (`1080p→4K`) are left alone. Larger-than-UHD canvases are not downscaled. Filenames stay the same; the sidecar records `bilinear_to_4k` / `bilinear_applied` / `output_wxh`.

Output frame rate = source frame rate. No interpolation. The FFmpeg conform step restamps the mpv intermediate to the source rate (`-r` on input + output, `-fps_mode cfr`). That is timestamp rewrite only — it does not blend or invent frames. Audio is `-c:a copy` when present; if copy fails, audio is dropped. Audio is never re-encoded as a blocker.

## Token table

Device tags in filenames: **`4080`** (RTX 4080 Super only) and **`LAPTOP`** (Mac Air M4 + Surface Laptop 4 share one dump). Never emit `_SURF` or `_MAC`.

Group **A** (standard) defaults **ON**. Group **B** (anime / drawing) has a master switch default **OFF**. If Group B is enabled, Group A still runs unless you uncheck it — animation files therefore get **standard + anime** outputs.

| UI label | Token (filename) | Device represented | Content | What it is | Backend |
|---|---|---|---|---|---|
| FSRCNNX ×2 16 — RTX 4080 Super | `FSRCNNX16_4080` | RTX 4080 Super | Live action / general (also run on anime by default) | Best live-action shader the 4080 can run live. igv FSRCNNX_x2_16-0-4-1 | plain mpv + shader |
| FSRCNNX ×2 56 — RTX 4080 Super | `FSRCNNX56_4080` | RTX 4080 Super | Live action / general (also run on anime by default) | Heavier igv FSRCNNX_x2_56-16-4-1. Same family as 16, larger network. 4080-only — not the laptop profile | plain mpv + shader |
| FSRCNNX ×2 8 — laptops (Mac + Surface) | `FSRCNNX8_LAPTOP` | MacBook Air M4 + Surface Laptop 4 | Live action / general (also run on anime by default) | Best live-action shader both laptops can run live. igv FSRCNNX_x2_8-0-4-1 | plain mpv + shader |
| Anime4K Fast Mode A — laptops (Mac + Surface) | `ANIME4KFAST_LAPTOP` | MacBook Air M4 + Surface Laptop 4 | 2D animation / line art | Best Anime4K chain both laptops can run live. v4 Fast Mode A (S/M shaders), not HQ/VL | plain mpv + shader chain |
| AnimeJaNai Balanced — RTX 4080 Super | `ANIMEJANAI_BAL_4080` | RTX 4080 Super | 2D animation | Best anime model the 4080 can run live. 2x_AnimeJaNai HD V3 Balanced. Not a laptop profile | AnimeJaNai mpv |

Content tag **Live / Animation** is visual + summary only. It does **not** by itself enable Group B — the Group B switch does.

Not implemented (on purpose): NVIDIA RTX VSR, Lanczos, Infuse, MetalFX, Topaz, Real-ESRGAN x4plus, any 1.5× path. Bilinear exists only as the optional post-2× FFmpeg scale to 4K above — it is not a catalog model.

## Filename nomenclature

Input `v-beauty-1080p-24fps.mp4` → outputs in the chosen output folder:

```
v-beauty-1080p-24fps-FSRCNNX16_4080.mp4
v-beauty-1080p-24fps-FSRCNNX56_4080.mp4
v-beauty-1080p-24fps-FSRCNNX8_LAPTOP.mp4
v-beauty-1080p-24fps-ANIME4KFAST_LAPTOP.mp4
v-beauty-1080p-24fps-ANIMEJANAI_BAL_4080.mp4
```

- Keep the **full original stem** (everything before the last `.`).
- Append `-` + **token** from the table. Always `.mp4`.
- Tokens are uppercase, no spaces. No extra quality words (`fast`, `crf12`) in the filename — CRF belongs in the log / `*.dump.json` sidecar.
- Existing targets are **skipped** unless **Overwrite existing** is checked.
- Source files are never overwritten.

Each MP4 gets a sidecar `*.dump.json` (source path, WxH in/out, fps, token, device, backend command, ffmpeg command, UTC start/end, elapsed seconds, output bytes, bilinear_to_4k / bilinear_applied). After a batch, `dump_manifest.csv` is written in the output folder.

## Encode pipeline

1. **Render** with mpv to a near-lossless intermediate in tmp (`libx264 crf=16 preset=fast` MKV). GLSL models use `--glsl-shaders=` with `;` on Windows. Output canvas is forced to the 2× target (`--vf=gpu-next=w=W:h=H`).
2. **Conform** with the thesis FFmpeg to MP4:

```
ffmpeg -y -r <source_fps> -i TMP -i INPUT
  -map 0:v:0 -map 1:a:0?
  [-vf scale=3840:2160:flags=bilinear]   # only when Bilinear to 4K is on and 2× is not already 4K
  -c:v libx265 -crf 12 -preset medium -pix_fmt yuv420p -tag:v hvc1
  -c:a copy
  -r <source_fps> -fps_mode cfr
  -movflags +faststart
  OUTPUT.mp4
```

`<source_fps>` is the probed `r_frame_rate` fraction (e.g. `24/1`). mpv 0.41 dropped `--ofps`, and `vf=gpu` on heavy 4K shader dumps can stretch timestamps (24.0000 → 23.8333) even when the frame count matches the source. Input `-r` ignores those timestamps and assigns source-rate PTS in decode order.

CRF **12** is mandatory for the delivered file unless you explicitly **Unlock CRF** in Settings (warning shown). Intermediate CRF 16 is never used as the final quality.

After encode, ffprobe must report the expected canvas (±0) and source fps (tolerance 0.05) or the job fails. Expected canvas is the 2× target, or **3840×2160** when bilinear-to-4K applied. One failure does not abort the queue.

Cancel kills the current mpv/ffmpeg **process tree** (`CREATE_NEW_PROCESS_GROUP` + `taskkill /PID /T`). Partial tmp files are left on cancel/fail and deleted on success.

Jobs run **sequentially** on the GPU. Optional **two parallel GLSL jobs** is off by default (VRAM). AnimeJaNai always takes exclusive GPU access.

## How to add shaders

Place these files in the shaders directory (default `C:\Tools\mpv\portable_config\shaders\`). If a file is missing, that model row is marked **Missing** with the expected filename and Start refuses that model.

**FSRCNNX** (16 and 8: exact names, case-insensitive. 56 also accepts the underscore-on-disk name):

- `FSRCNNX_x2_16-0-4-1.glsl`
- `FSRCNNX_x2_56-16-4-1.glsl` (or `FSRCNNX_x2_56_16_4_1.glsl` on disk)
- `FSRCNNX_x2_8-0-4-1.glsl`

FSRCNNX 56 is **4080-only** (token `FSRCNNX56_4080`). It is not a Mac/Surface live preset. If that file is missing, only the 56 row is refused; 16 and 8 still run. Do not stack 56 as a 4× pass — output stays locked to 2×.

Upstream: [igv/FSRCNN-TensorFlow](https://github.com/igv/FSRCNN-TensorFlow) (the GLSL hooks commonly shipped as `FSRCNNX_x2_16-0-4-1.glsl` / `FSRCNNX_x2_56-16-4-1.glsl` / `FSRCNNX_x2_8-0-4-1.glsl`).

**Anime4K Fast Mode A** (exact order; Windows mpv separator is `;`):

1. `Anime4K_Clamp_Highlights.glsl`
2. `Anime4K_Restore_CNN_M.glsl`
3. `Anime4K_Upscale_CNN_x2_M.glsl`
4. `Anime4K_AutoDownscalePre_x2.glsl`
5. `Anime4K_Upscale_CNN_x2_S.glsl`

Upstream: [bloc97/Anime4K](https://github.com/bloc97/Anime4K). If a filename on disk differs slightly (e.g. `Anime4K_Restore_CNN_Moderate_M.glsl`), the app resolves by prefix / inserted-word match and logs the resolved list. If the chain cannot be resolved, that model fails with the file list — it is not skipped silently.

**AnimeJaNai** does **not** use GLSL. Install the [mpv-AnimeJaNai](https://github.com/the-database/mpv-AnimeJaNai) bundle at `C:\Tools\mpv-AnimeJaNai\` and point Settings at its `mpv.exe`. The app writes `animejanai_balanced.conf` and passes `--include` so the bundled **Balanced** profile (Shift+2 / slot 1002) loads. TensorRT engine builds once and can take several minutes.

## GUI notes

- Queue: add files / add folder / drag-and-drop. Columns: file, WxH, fps, duration, class, content tag, status.
- Group A has three checkboxes, default ON: FSRCNNX 16, FSRCNNX 56 (heavier than 16, RTX 4080 Super only — not the laptop profile), FSRCNNX 8.
- Content tag default **Live**. Setting rows to **Animation** does not enable Group B.
- **Bilinear to 4K when not 4K** (default off): after the native 2× dump, FFmpeg bilinear-scales to 3840×2160 when the 2× canvas is smaller. 1080p→4K files are unchanged.
- Dry-run prints the exact mpv + ffmpeg commands and writes no video.
- Status bar: ffmpeg / mpv / AnimeJaNai / GPU (`nvidia-smi`).
- FPS other than 24/25/30/50/60 (plus 23.976 / 29.97 / 59.94) warns but still allows.

## Play live

Play live is for watts / dropped-frame checks against Chrome and across models. **Decode is split by live mode** (intentional — two different GPU paths). Dumps remain `--hwdec=no` and `--vo=lavc` for a deterministic encode. Live never uses `--untimed`.

The extra GPU copy is a cost of **hooking client shaders**, not of watching the file. A fair no-AI baseline therefore must not pay that copy. Chrome + RTX VSR also stays on a zero-copy video path inside the driver. Comparing NONE+copy to Chrome would still blame mpv for a cost the AI models need and Chrome does not pay when no VSR-equivalent shader is attached.

### `LIVE_NONE` — None — native (no AI)

Hardware decode that matches `vo=gpu-next` + D3D11 (what Chrome uses on Windows):

```
--hwdec=d3d11va
--gpu-api=d3d11
```

Do **not** pass `--hwdec=nvdec` on this preset: raw CUDA nvdec often fails silently under gpu-next/d3d11 and mpv stays on software (`${hwdec-current}` = `no`) — that is the high CPU watts. No `-copy` on the first try, no `--glsl-shaders`, no AnimeJaNai filter, no `--vf=gpu=w=…:h=…`, no `--untimed`. `--vo=gpu-next --force-window=yes` and audio stay on. If `d3d11va` fails to init, fall back once to `d3d11va-copy`, then `nvdec-copy`. Never stay on `hwdec=no` without a red log line (`hwdec-current=no`). The log prints the intended `--hwdec=` / `--gpu-api=` after spawn and the decoder that actually attached.

Log line: `live NONE intended --hwdec=d3d11va --gpu-api=d3d11 (no copy, Chrome-like baseline)`.

In the mpv console, `print-text ${hwdec-current}` must show `d3d11va` for NONE, not `no`.

`LIVE_NONE` is live-only: plain `mpv.exe`, native window scale like a browser. Use it as the watts baseline vs Chrome. It is not a dump catalog token. Unsupported 2× heights are allowed in this mode.

### Catalog AI models (FSRCNNX 16/56/8, Anime4K Fast, AnimeJaNai, …)

Keep the shader-safe copy path:

```
--hwdec=nvdec-copy
```

Fallback `d3d11va-copy`. Do not switch these to plain `nvdec`: GLSL / `vf=gpu` / AnimeJaNai need the copy. Catalog models still force 2× (`--vf=gpu=w=…:h=…` for GLSL; AnimeJaNai via the bundle filter + autofit).

Log line: `live TOKEN intended --hwdec=nvdec-copy (copy required for shaders)`.

Select **one** queue file (or the first playable among a multi-selection), pick a **Live model** in the combobox (every catalog token, including Group B even if the dump switch is off), then **Play live**. That opens a visible mpv window using the **same binary and shaders as the dump** — GLSL models via plain `mpv.exe --glsl-shaders=`, AnimeJaNai via the bundle `mpv.exe --config-dir=` (no `--no-config`; CLI `--hwdec=` wins over the bundle). Audio stays on. No MP4, sidecar, or manifest row is written.

Stop with **Stop live** or by closing the mpv window. A dump batch and live playback cannot share the GPU: Play live is refused while dumps run; Start dumps offers to stop a live window first.

In mpv, **Shift+I** opens the profiler (frame times / shader cost). Settings may persist `live_hwdec` (`nvdec-copy` / `auto-copy` / `no`) for **AI live sessions only**; dumps ignore that key. `LIVE_NONE` stays locked to `d3d11va` + `--gpu-api=d3d11` unless **Force copy on None baseline** is checked (default off).

## Project layout

```
clientsr-dump-lab/
  README.md
  LICENSE
  requirements.txt
  pyproject.toml
  app/
    main.py
    gui.py
    settings.py
    probe.py
    naming.py
    catalog.py
    jobs.py
    backend_mpv.py
    backend_animejanai.py
    backend_live.py
    encode.py
    manifest.py
    winproc.py
```

## License

Copyright (C) 2026 Adriano Da Silva

ClientSR Dump Lab is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

See [LICENSE](LICENSE) for the full terms.
