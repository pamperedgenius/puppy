"""python -m puppy: PTY pass-through proxy with a parallel VT parser, for debugging.

Runs your shell normally — input/output pass straight through like a real terminal —
while every byte from the child is also fed through the Parser into a Screen model.
Press Ctrl+] to dump the Screen's current grid to stderr without disturbing the
live session, to compare the parser's model against what actually printed.
"""
from __future__ import annotations

import os
import select
import shutil
import sys
import termios
import tty

from .parser import Parser
from .pty_session import PtySession
from .screen import Screen

DUMP_KEY = 0x1D  # Ctrl+]


def main() -> None:
    cols, rows = shutil.get_terminal_size(fallback=(80, 24))
    screen = Screen(rows, cols)
    parser = Parser(screen)
    session = PtySession(rows=rows, cols=cols)

    stdin_fd = sys.stdin.fileno()
    old_termios = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)
    try:
        while session.alive():
            readable, _, _ = select.select([stdin_fd, session.master_fd], [], [], 0.1)
            if stdin_fd in readable:
                data = os.read(stdin_fd, 4096)
                if not data:
                    break
                if DUMP_KEY in data:
                    dump = screen.dump_text().replace("\n", "\r\n")
                    sys.stderr.write(f"\r\n--- puppy screen dump ---\r\n{dump}\r\n--- end dump ---\r\n")
                    data = data.replace(bytes([DUMP_KEY]), b"")
                if data:
                    session.write(data)
            if session.master_fd in readable:
                try:
                    data = os.read(session.master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                parser.feed(data)
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)
        session.close()


if __name__ == "__main__":
    main()
