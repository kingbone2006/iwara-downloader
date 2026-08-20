"""Windows graphics preference + list physical GPUs for UI settings."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


# Windows DirectX UserGpuPreferences:
# 0 = auto / let Windows decide
# 1 = power saving (usually iGPU)
# 2 = high performance (usually discrete GPU)
GPU_AUTO = "auto"
GPU_HIGH = "high_performance"
GPU_SAVE = "power_saving"

VALID_GPU_MODES = (GPU_AUTO, GPU_HIGH, GPU_SAVE)

# Adapter-specific selection keys: "adapter:0", "adapter:1", ...
_ADAPTER_KEY_RE = re.compile(r"^adapter:(\d+)$", re.I)


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    vram_mb: int
    driver: str
    pnp_id: str
    is_igpu: bool

    @property
    def key(self) -> str:
        return f"adapter:{self.index}"

    def short_label(self) -> str:
        kind = "iGPU" if self.is_igpu else "dGPU"
        if self.vram_mb > 0:
            # Show GB when ≥ 1024 MB for readability
            if self.vram_mb >= 1024:
                gb = self.vram_mb / 1024.0
                vram = f"{gb:.0f} GB" if abs(gb - round(gb)) < 0.05 else f"{gb:.1f} GB"
            else:
                vram = f"{self.vram_mb} MB"
            return f"{self.name}  ·  {vram}  ·  {kind}"
        return f"{self.name}  ·  {kind}"


def python_exe_path() -> str:
    return str(Path(sys.executable).resolve())


def _looks_like_igpu(name: str) -> bool:
    n = (name or "").lower()
    # Explicit discrete markers first
    discrete_hints = (
        "geforce",
        "rtx ",
        " gtx",
        "quadro",
        "tesla",
        "cmp ",
        "radeon rx",
        "radeon pro",
        "radeon r7",
        "radeon r9",
        "radeon hd",
        "arc a",
        "firepro",
    )
    if any(h in n for h in discrete_hints):
        return False
    igpu_hints = (
        "intel(r) uhd",
        "intel(r) hd",
        "intel(r) iris",
        "intel uhd",
        "intel hd",
        "intel iris",
        "uhd graphics",
        "iris xe",
        "iris plus",
        "radeon graphics",  # APU iGPU naming
        "radeon(tm) graphics",
        "amd radeon(tm) graphics",
        "microsoft basic render",
        "basic display",
    )
    if any(h in n for h in igpu_hints):
        return True
    # Intel without discrete keywords → likely iGPU
    if "intel" in n and "arc" not in n:
        return True
    return False


def _bytes_to_mb(n: int) -> int:
    if n <= 0:
        return 0
    mb = int(n) // (1024 * 1024)
    # Cap absurd values (shared system RAM mis-reports etc.)
    if mb > 256 * 1024:
        return 0
    return mb


def _vram_mb_from_wmi(raw: object) -> int:
    """
    Win32_VideoController.AdapterRAM is a 32-bit field — GPUs with ≥4 GB
    often report ~4095 MB (overflow). Prefer registry QWORD when available.
    """
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    # Classic overflow pattern: values just under 2^32
    if n >= 0xFFFF0000:
        # Treat as unknown; caller should use registry QWORD
        return 0
    return _bytes_to_mb(n)


def _vram_from_registry() -> dict[str, int]:
    """
    Read dedicated VRAM from display-class driver keys (64-bit QWORD).

    Path: HKLM\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-...}\\NNNN
    Value: HardwareInformation.qwMemorySize (REG_QWORD) — correct for 8GB+ GPUs.
    Returns map of lowercase driver name → VRAM MB.
    """
    if sys.platform != "win32":
        return {}
    try:
        import winreg  # type: ignore
    except ImportError:
        return {}

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    out: dict[str, int] = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return {}
    try:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
                i += 1
            except OSError:
                break
            if not sub.isdigit():
                continue
            try:
                sk = winreg.OpenKey(root, sub)
            except OSError:
                continue
            try:
                try:
                    desc, _ = winreg.QueryValueEx(sk, "DriverDesc")
                except FileNotFoundError:
                    desc = ""
                name = str(desc or "").strip()
                if not name:
                    continue
                vram_b = 0
                try:
                    qw, _ = winreg.QueryValueEx(sk, "HardwareInformation.qwMemorySize")
                    vram_b = int(qw or 0)
                except FileNotFoundError:
                    try:
                        dw, _ = winreg.QueryValueEx(sk, "HardwareInformation.MemorySize")
                        vram_b = int(dw or 0)
                        # Same 32-bit trap as WMI for 4GB+
                        if vram_b >= 0xFFFF0000:
                            vram_b = 0
                    except FileNotFoundError:
                        vram_b = 0
                mb = _bytes_to_mb(vram_b)
                if mb > 0:
                    out[name.lower()] = mb
                    # Also index simplified variants for fuzzy match
                    simplified = (
                        name.lower()
                        .replace("(tm)", "")
                        .replace("(r)", "")
                        .replace("  ", " ")
                        .strip()
                    )
                    out[simplified] = mb
            finally:
                winreg.CloseKey(sk)
    finally:
        winreg.CloseKey(root)
    return out


def _match_reg_vram(name: str, reg_map: dict[str, int]) -> int:
    if not name or not reg_map:
        return 0
    low = name.lower().strip()
    if low in reg_map:
        return reg_map[low]
    simple = low.replace("(tm)", "").replace("(r)", "").replace("  ", " ").strip()
    if simple in reg_map:
        return reg_map[simple]
    # Substring match (WMI name vs registry DriverDesc differ slightly)
    best = 0
    for key, mb in reg_map.items():
        if key in low or low in key:
            best = max(best, mb)
    return best


def _vram_from_nvidia_smi() -> dict[str, int]:
    """Optional: nvidia-smi for accurate NVIDIA totals (MB)."""
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return {}
        out: dict[str, int] = {}
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                mb = int(float(parts[1]))
            except ValueError:
                continue
            if mb > 0:
                out[parts[0].lower()] = mb
        return out
    except Exception:
        return {}


def list_gpus() -> list[GpuInfo]:
    """
    Enumerate display adapters on this PC (live — never hard-coded).

    Name/driver: WMI Win32_VideoController.
    VRAM: registry HardwareInformation.qwMemorySize (64-bit) preferred,
    then nvidia-smi, then WMI AdapterRAM (32-bit, often wrong for ≥4 GB).
    """
    if sys.platform != "win32":
        return []
    ps = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM, DriverVersion, PNPDeviceID, Status | "
        "ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (r.stdout or "").strip()
        if not text:
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    reg_vram = _vram_from_registry()
    nv_vram = _vram_from_nvidia_smi()

    out: list[GpuInfo] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        if not name:
            continue
        # Skip ghost / remote display adapters
        low = name.lower()
        if "microsoft remote" in low or ("virtual" in low and "box" in low):
            continue

        # Prefer 64-bit registry / nvidia-smi over WMI 32-bit AdapterRAM
        vram = _match_reg_vram(name, reg_vram)
        if vram <= 0 and nv_vram:
            vram = _match_reg_vram(name, nv_vram)
            if vram <= 0:
                # NVIDIA short names e.g. "NVIDIA CMP 40HX" vs full WMI name
                for nk, mb in nv_vram.items():
                    if nk in low or any(
                        tok in low for tok in nk.replace("nvidia ", "").split() if len(tok) > 3
                    ):
                        vram = max(vram, mb)
        if vram <= 0:
            vram = _vram_mb_from_wmi(row.get("AdapterRAM"))

        out.append(
            GpuInfo(
                index=len(out),
                name=name,
                vram_mb=vram,
                driver=str(row.get("DriverVersion") or "").strip(),
                pnp_id=str(row.get("PNPDeviceID") or "").strip(),
                is_igpu=_looks_like_igpu(name),
            )
        )
    return out


def format_gpu_list(gpus: list[GpuInfo] | None = None) -> str:
    """Human-readable multi-line list of adapters on this PC."""
    gpus = list_gpus() if gpus is None else gpus
    if not gpus:
        return "(không phát hiện GPU / not Windows)"
    lines = []
    for g in gpus:
        kind = "tích hợp (iGPU)" if g.is_igpu else "rời (dGPU)"
        if g.vram_mb >= 1024:
            gb = g.vram_mb / 1024.0
            vram = f"{gb:.0f} GB" if abs(gb - round(gb)) < 0.05 else f"{gb:.1f} GB"
        elif g.vram_mb > 0:
            vram = f"{g.vram_mb} MB"
        else:
            vram = "?"
        lines.append(f"  [{g.index}] {g.name}  ·  VRAM {vram}  ·  {kind}")
        if g.driver:
            lines.append(f"       driver {g.driver}")
    return "\n".join(lines)


def adapter_key(index: int) -> str:
    return f"adapter:{int(index)}"


def parse_gpu_selection(
    selection: str, gpus: list[GpuInfo] | None = None
) -> tuple[str, str]:
    """
    Resolve UI selection to (mode, adapter_name).

    selection:
      auto | high_performance | power_saving | adapter:N
    """
    gpus = list_gpus() if gpus is None else gpus
    sel = (selection or GPU_AUTO).strip()
    low = sel.lower()

    if low in VALID_GPU_MODES:
        return low, ""

    m = _ADAPTER_KEY_RE.match(sel)
    if m:
        idx = int(m.group(1))
        for g in gpus:
            if g.index == idx:
                mode = GPU_SAVE if g.is_igpu else GPU_HIGH
                return mode, g.name
        # stale index
        return GPU_AUTO, ""

    # Legacy: raw adapter name stored as selection
    for g in gpus:
        if g.name == sel:
            mode = GPU_SAVE if g.is_igpu else GPU_HIGH
            return mode, g.name

    return GPU_AUTO, ""


def selection_label(selection: str, t_auto: str, t_high: str, t_save: str) -> str:
    """Display label for current selection (for OptionMenu)."""
    low = (selection or GPU_AUTO).strip().lower()
    if low == GPU_AUTO:
        return t_auto
    if low == GPU_HIGH:
        return t_high
    if low == GPU_SAVE:
        return t_save
    gpus = list_gpus()
    m = _ADAPTER_KEY_RE.match(selection or "")
    if m:
        idx = int(m.group(1))
        for g in gpus:
            if g.index == idx:
                return g.short_label()
    for g in gpus:
        if g.name == selection:
            return g.short_label()
    return t_auto


def build_gpu_options(
    t_auto: str, t_high: str, t_save: str, gpus: list[GpuInfo] | None = None
) -> list[tuple[str, str]]:
    """
    Ordered (key, label) for dropdowns.
    Modes first, then each physical adapter.
    """
    gpus = list_gpus() if gpus is None else gpus
    opts: list[tuple[str, str]] = [
        (GPU_AUTO, t_auto),
        (GPU_HIGH, t_high),
        (GPU_SAVE, t_save),
    ]
    for g in gpus:
        opts.append((g.key, g.short_label()))
    return opts


def apply_windows_gpu_preference(selection: str) -> tuple[bool, str]:
    """
    Set per-app GPU preference for this Python interpreter (Windows).
    `selection` may be a mode or adapter:N.
    Returns (ok, message). No-op on non-Windows.
    """
    gpus = list_gpus()
    mode, adapter_name = parse_gpu_selection(selection, gpus)

    if sys.platform != "win32":
        return True, f"GPU preference skipped (not Windows): {mode}"

    try:
        import winreg  # type: ignore
    except ImportError:
        return False, "winreg unavailable"

    exe = python_exe_path()
    targets = [exe]
    try:
        p = Path(exe)
        pyw = p.with_name("pythonw.exe")
        if pyw.is_file():
            targets.append(str(pyw.resolve()))
    except Exception:
        pass

    key_path = r"Software\Microsoft\DirectX\UserGpuPreferences"
    value_map = {
        GPU_AUTO: "GpuPreference=0;",
        GPU_SAVE: "GpuPreference=1;",
        GPU_HIGH: "GpuPreference=2;",
    }
    pref = value_map.get(mode, value_map[GPU_AUTO])

    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        )
        try:
            for t in targets:
                if mode == GPU_AUTO:
                    try:
                        winreg.DeleteValue(key, t)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass
                else:
                    winreg.SetValueEx(key, t, 0, winreg.REG_SZ, pref)
        finally:
            winreg.CloseKey(key)
    except OSError as exc:
        return False, f"Không ghi được GPU preference: {exc}"

    labels = {
        GPU_AUTO: "Tự động (Windows)",
        GPU_HIGH: "Hiệu năng cao (GPU rời)",
        GPU_SAVE: "Tiết kiệm điện (GPU tích hợp)",
    }
    mode_txt = labels.get(mode, mode)
    if adapter_name:
        return True, f"GPU UI: {mode_txt} · chọn «{adapter_name}» → {exe}"
    detected = ", ".join(g.name for g in gpus) if gpus else "—"
    return True, f"GPU UI: {mode_txt} → {exe}  |  máy: {detected}"


def flet_env_for_gpu(selection: str) -> dict[str, str]:
    """Extra env vars before launching Flet (Flutter)."""
    env: dict[str, str] = {}
    mode, _adapter = parse_gpu_selection(selection)
    if mode == GPU_HIGH:
        env["FLET_FORCE_WEB_SERVER"] = "false"
        env["LIBGL_ALWAYS_SOFTWARE"] = "0"
    elif mode == GPU_SAVE:
        env["LIBGL_ALWAYS_SOFTWARE"] = "0"
    return env


# Back-compat alias
VALID_GPU = VALID_GPU_MODES
