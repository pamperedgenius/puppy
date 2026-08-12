"""In-memory VT screen buffer: a grid of cells, cursor, and basic SGR attributes.

No rendering, no PTY awareness — a Screen is just a model that a Parser writes into.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Cell:
    char: str = " "
    fg: int | tuple[int, int, int] | None = None
    bg: int | tuple[int, int, int] | None = None
    bold: bool = False
    underline: bool = False
    reverse: bool = False


class Screen:
    _SGR_COLORS_FG = set(range(30, 38)) | set(range(90, 98))
    _SGR_COLORS_BG = set(range(40, 48)) | set(range(100, 108))

    def __init__(self, rows: int = 24, cols: int = 80) -> None:
        self.rows = rows
        self.cols = cols
        self.cursor_row = 0
        self.cursor_col = 0
        self._sgr: dict = self._default_sgr()
        self.grid: list[list[Cell]] = self._blank_grid(rows, cols)

    @staticmethod
    def _default_sgr() -> dict:
        return dict(fg=None, bg=None, bold=False, underline=False, reverse=False)

    @staticmethod
    def _blank_grid(rows: int, cols: int) -> list[list[Cell]]:
        return [[Cell() for _ in range(cols)] for _ in range(rows)]

    def resize(self, rows: int, cols: int) -> None:
        new_grid = self._blank_grid(rows, cols)
        for r in range(min(rows, self.rows)):
            for c in range(min(cols, self.cols)):
                new_grid[r][c] = self.grid[r][c]
        self.grid = new_grid
        self.rows, self.cols = rows, cols
        self.cursor_row = min(self.cursor_row, rows - 1)
        self.cursor_col = min(self.cursor_col, cols - 1)

    # --- writing text ---

    def put_char(self, ch: str) -> None:
        if self.cursor_col >= self.cols:
            self.cursor_col = 0
            self.linefeed()
        self.grid[self.cursor_row][self.cursor_col] = Cell(char=ch, **self._sgr)
        self.cursor_col += 1

    def linefeed(self) -> None:
        if self.cursor_row == self.rows - 1:
            self.grid.pop(0)
            self.grid.append([Cell() for _ in range(self.cols)])
        else:
            self.cursor_row += 1

    def carriage_return(self) -> None:
        self.cursor_col = 0

    def backspace(self) -> None:
        if self.cursor_col > 0:
            self.cursor_col -= 1

    def tab(self) -> None:
        self.cursor_col = min(((self.cursor_col // 8) + 1) * 8, self.cols - 1)

    # --- cursor movement (CSI) ---

    def cursor_up(self, n: int = 1) -> None:
        self.cursor_row = max(0, self.cursor_row - n)

    def cursor_down(self, n: int = 1) -> None:
        self.cursor_row = min(self.rows - 1, self.cursor_row + n)

    def cursor_forward(self, n: int = 1) -> None:
        self.cursor_col = min(self.cols - 1, self.cursor_col + n)

    def cursor_back(self, n: int = 1) -> None:
        self.cursor_col = max(0, self.cursor_col - n)

    def cursor_position(self, row: int = 1, col: int = 1) -> None:
        self.cursor_row = max(0, min(self.rows - 1, row - 1))
        self.cursor_col = max(0, min(self.cols - 1, col - 1))

    # --- erase ---

    def erase_in_display(self, mode: int = 0) -> None:
        if mode == 0:
            self._erase_line_from(self.cursor_row, self.cursor_col)
            for r in range(self.cursor_row + 1, self.rows):
                self._erase_line_from(r, 0)
        elif mode == 1:
            for r in range(0, self.cursor_row):
                self._erase_line_from(r, 0)
            self._erase_line_from(self.cursor_row, 0, self.cursor_col + 1)
        elif mode in (2, 3):
            self.grid = self._blank_grid(self.rows, self.cols)

    def erase_in_line(self, mode: int = 0) -> None:
        if mode == 0:
            self._erase_line_from(self.cursor_row, self.cursor_col)
        elif mode == 1:
            self._erase_line_from(self.cursor_row, 0, self.cursor_col + 1)
        elif mode == 2:
            self._erase_line_from(self.cursor_row, 0)

    def _erase_line_from(self, row: int, start_col: int, end_col: int | None = None) -> None:
        end = self.cols if end_col is None else end_col
        for c in range(start_col, end):
            self.grid[row][c] = Cell()

    # --- SGR (colors/attributes) ---

    def sgr(self, params: list[int]) -> None:
        if not params:
            params = [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self._sgr = self._default_sgr()
            elif p == 1:
                self._sgr["bold"] = True
            elif p == 4:
                self._sgr["underline"] = True
            elif p == 7:
                self._sgr["reverse"] = True
            elif p == 22:
                self._sgr["bold"] = False
            elif p == 24:
                self._sgr["underline"] = False
            elif p == 27:
                self._sgr["reverse"] = False
            elif p == 39:
                self._sgr["fg"] = None
            elif p == 49:
                self._sgr["bg"] = None
            elif p in self._SGR_COLORS_FG:
                self._sgr["fg"] = p
            elif p in self._SGR_COLORS_BG:
                self._sgr["bg"] = p
            elif p in (38, 48) and i + 1 < len(params):
                target = "fg" if p == 38 else "bg"
                mode = params[i + 1]
                if mode == 5 and i + 2 < len(params):
                    self._sgr[target] = params[i + 2]
                    i += 2
                elif mode == 2 and i + 4 < len(params):
                    self._sgr[target] = (params[i + 2], params[i + 3], params[i + 4])
                    i += 4
            i += 1

    # --- debug ---

    def dump_text(self) -> str:
        return "\n".join("".join(cell.char for cell in row).rstrip() for row in self.grid)
