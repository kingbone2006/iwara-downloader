"""Entry point for Iwara Downloader GUI (+ console debug when run from cmd/bat)."""

from __future__ import annotations

import os
import sys

from app.core.debug_log import console_error, console_log


def main() -> int:
    console_log("=== Iwara Downloader starting ===")
    console_log(f"Python {sys.version.split()[0]} | cwd={sys.path[0]}")
    try:
        from app.core.config import AppConfig
        from app.core.gpu_pref import apply_windows_gpu_preference, flet_env_for_gpu

        cfg = AppConfig.load()
        backend = (cfg.ui_backend or "tk").strip().lower()
        gpu = (cfg.ui_gpu or "auto").strip().lower()

        # Apply Windows GPU preference early (affects Flet/Flutter process)
        ok, msg = apply_windows_gpu_preference(gpu if backend == "flet" else "auto")
        console_log(msg if ok else f"GPU pref: {msg}")

        if backend == "flet":
            for k, v in flet_env_for_gpu(gpu).items():
                os.environ.setdefault(k, v)
            console_log(f"UI backend: Flet/Flutter (GPU) · gpu={gpu}")
            try:
                from app.ui.app_flet import run_app

                run_app()
            except Exception as exc:  # noqa: BLE001
                console_error(
                    "Flet UI failed — falling back to CustomTkinter (CPU)",
                    exc,
                )
                from app.ui.app import run_app as run_tk

                run_tk()
        else:
            console_log("UI backend: CustomTkinter (CPU) · list paging enabled")
            from app.ui.app import run_app

            run_app()

        console_log("=== App closed normally ===")
        return 0
    except Exception as exc:  # noqa: BLE001
        console_error("Fatal error — app crashed", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
