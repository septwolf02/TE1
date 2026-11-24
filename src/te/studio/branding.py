"""Load the Studio icon from package data in source and frozen builds."""

from importlib.resources import as_file, files
import os
import tkinter as tk

from PIL import Image, ImageTk

APP_NAME = "TE Studio"
WINDOWS_APP_ID = "TEStudio.Production"


def icon_resource():
    return files("te").joinpath("branding", "te-studio.ico")


def set_windows_app_id() -> None:
    """Match the installer shortcuts before Tk creates any windows."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)


def apply_window_branding(root: tk.Tk) -> None:
    if os.name == "nt":
        with as_file(icon_resource()) as icon:
            root.iconbitmap(default=str(icon))
    else:
        root.iconphoto(True, logo_photo(root, 48))


def logo_photo(window: tk.Misc, size: int) -> ImageTk.PhotoImage:
    with icon_resource().open("rb") as stream, Image.open(stream) as image:
        logo = image.resize((size, size), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(logo, master=window)
