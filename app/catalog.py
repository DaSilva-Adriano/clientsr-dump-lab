"""Model catalog. Tokens and device tags are the only names that go into filenames.

Device tags: 4080 (RTX 4080 Super dump host) and LAPTOP (MacBook Air M4 + Surface
Laptop 4 share the same dumped file). Never emit _SURF or _MAC.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BackendKind = Literal["mpv_glsl", "animejanai"]
GroupKind = Literal["A", "B"]
DeviceTag = Literal["4080", "LAPTOP"]


@dataclass(frozen=True)
class ModelSpec:
    token: str
    ui_label: str
    device_tag: DeviceTag
    device_label: str
    content: str
    purpose: str
    helper: str
    group: GroupKind
    backend: BackendKind
    shaders: tuple[str, ...] = ()
    default_enabled: bool = False
    content_group: str = "standard"


# Filename tokens — keep in sync with README.
TOKEN_FSRCNNX16_4080 = "FSRCNNX16_4080"
TOKEN_FSRCNNX8_LAPTOP = "FSRCNNX8_LAPTOP"
TOKEN_ANIME4KFAST_LAPTOP = "ANIME4KFAST_LAPTOP"
TOKEN_ANIMEJANAI_BAL_4080 = "ANIMEJANAI_BAL_4080"

FSRCNNX16_SHADER = "FSRCNNX_x2_16-0-4-1.glsl"
FSRCNNX8_SHADER = "FSRCNNX_x2_8-0-4-1.glsl"

# Exact Fast Mode A chain. On disk, names may differ slightly and are resolved
# by prefix / inserted-word match in backend_mpv.resolve_shader_chain.
ANIME4K_FAST_MODE_A: tuple[str, ...] = (
    "Anime4K_Clamp_Highlights.glsl",
    "Anime4K_Restore_CNN_M.glsl",
    "Anime4K_Upscale_CNN_x2_M.glsl",
    "Anime4K_AutoDownscalePre_x2.glsl",
    "Anime4K_Upscale_CNN_x2_S.glsl",
)

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        token=TOKEN_FSRCNNX16_4080,
        ui_label="FSRCNNX ×2 16 — RTX 4080 Super",
        device_tag="4080",
        device_label="RTX 4080 Super",
        content="Live action / general (also run on anime by default)",
        purpose="Best live-action shader the 4080 can run live. igv FSRCNNX_x2_16-0-4-1",
        helper="RTX 4080 Super · FSRCNNX 16-tap · best live-action shader the 4080 can run live",
        group="A",
        backend="mpv_glsl",
        shaders=(FSRCNNX16_SHADER,),
        default_enabled=True,
        content_group="standard",
    ),
    ModelSpec(
        token=TOKEN_FSRCNNX8_LAPTOP,
        ui_label="FSRCNNX ×2 8 — laptops (Mac + Surface)",
        device_tag="LAPTOP",
        device_label="MacBook Air M4 + Surface Laptop 4",
        content="Live action / general (also run on anime by default)",
        purpose="Best live-action shader both laptops can run live. igv FSRCNNX_x2_8-0-4-1",
        helper="Mac Air M4 + Surface · FSRCNNX 8-tap · one dump for both laptops",
        group="A",
        backend="mpv_glsl",
        shaders=(FSRCNNX8_SHADER,),
        default_enabled=True,
        content_group="standard",
    ),
    ModelSpec(
        token=TOKEN_ANIME4KFAST_LAPTOP,
        ui_label="Anime4K Fast Mode A — laptops (Mac + Surface)",
        device_tag="LAPTOP",
        device_label="MacBook Air M4 + Surface Laptop 4",
        content="2D animation / line art",
        purpose="Best Anime4K chain both laptops can run live. v4 Fast Mode A (S/M shaders), not HQ/VL",
        helper="Mac Air M4 + Surface · Anime4K v4 Fast Mode A (S/M) · one dump for both laptops",
        group="B",
        backend="mpv_glsl",
        shaders=ANIME4K_FAST_MODE_A,
        default_enabled=False,
        content_group="anime",
    ),
    ModelSpec(
        token=TOKEN_ANIMEJANAI_BAL_4080,
        ui_label="AnimeJaNai Balanced — RTX 4080 Super",
        device_tag="4080",
        device_label="RTX 4080 Super",
        content="2D animation",
        purpose="Best anime model the 4080 can run live. 2x_AnimeJaNai HD V3 Balanced. Not a laptop profile",
        helper="RTX 4080 Super · 2x_AnimeJaNai HD V3 Balanced · not a laptop profile",
        group="B",
        backend="animejanai",
        shaders=(),
        default_enabled=False,
        content_group="anime",
    ),
)

MODELS_BY_TOKEN: dict[str, ModelSpec] = {m.token: m for m in MODELS}

GROUP_A = tuple(m for m in MODELS if m.group == "A")
GROUP_B = tuple(m for m in MODELS if m.group == "B")

ALLOWED_DEVICE_TAGS = frozenset({"4080", "LAPTOP"})


def selected_models(
    group_a_checked: dict[str, bool],
    group_b_master: bool,
    group_b_checked: dict[str, bool],
) -> list[ModelSpec]:
    """Return models that will actually run given the UI state.

    Group A always honours its checkboxes. Group B is ignored entirely when
    the master switch is off.
    """
    out: list[ModelSpec] = []
    for spec in GROUP_A:
        if group_a_checked.get(spec.token, spec.default_enabled):
            out.append(spec)
    if group_b_master:
        for spec in GROUP_B:
            if group_b_checked.get(spec.token, spec.default_enabled):
                out.append(spec)
    return out
