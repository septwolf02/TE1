"""An advisory per-project editing lock shared by Production and Development."""
from __future__ import annotations

import os
from pathlib import Path


class ProjectEditLock:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        path = self.directory / "generated" / "cache" / ".studio-edit.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        try:
            if path.stat().st_size == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise ValueError("Dự án đang mở để chỉnh sửa trong TE Studio khác. Hãy đóng cửa sổ đó trước.") from exc

    def close(self) -> None:
        if self.stream.closed:
            return
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
