"""Spawn a real PTY running a shell; read/write/resize it."""
from __future__ import annotations

import fcntl
import os
import pty
import struct
import termios


class PtySession:
    def __init__(self, shell: str | None = None, rows: int = 24, cols: int = 80) -> None:
        self.shell = shell or os.environ.get("SHELL", "/bin/sh")
        self.pid, self.master_fd = pty.fork()
        if self.pid == 0:
            os.execvp(self.shell, [self.shell])
        self.resize(rows, cols)

    def resize(self, rows: int, cols: int) -> None:
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, packed)

    def read(self, n: int = 4096) -> bytes:
        return os.read(self.master_fd, n)

    def write(self, data: bytes) -> None:
        os.write(self.master_fd, data)

    def alive(self) -> bool:
        pid, _status = os.waitpid(self.pid, os.WNOHANG)
        return pid == 0

    def close(self) -> None:
        try:
            os.close(self.master_fd)
        except OSError:
            pass
