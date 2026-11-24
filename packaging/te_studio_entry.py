"""No-console entry point with local startup diagnostics."""
from __future__ import annotations

import faulthandler
import os
from pathlib import Path
import traceback


def diagnostic_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config") / "TEStudio" / "diagnostics"
    root.mkdir(parents=True, exist_ok=True)
    return root / "startup.log"


def main() -> None:
    path = diagnostic_path()
    with path.open("a", encoding="utf-8") as log:
        faulthandler.enable(log)
        try:
            from te.studio.gui import StudioApp
            import tkinter as tk
            root = tk.Tk()

            def callback_error(exc_type, exc, tb):
                traceback.print_exception(exc_type, exc, tb, file=log)
                log.flush()
                from tkinter import messagebox
                messagebox.showerror("TE Studio", f"Giao diện gặp lỗi. Xem log: {path}", parent=root)

            root.report_callback_exception = callback_error
            StudioApp(root)
            root.mainloop()
        except Exception:
            traceback.print_exc(file=log)
            log.flush()
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f"Không mở được TE Studio. Xem log: {path}",
                                                 "TE Studio", 0x10)
            except (AttributeError, OSError):
                pass
            raise


if __name__ == "__main__":
    main()
