"""In-memory VT screen buffer: a grid of cells, cursor, and basic SGR attributes.

No rendering, no PTY awareness — a Screen is just a model that a Parser writes into.
"""
from __future__ import annotations

from collections import deque
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

    def __init__(self, rows: int = 24, cols: int = 80, scrollback_limit: int = 2000) -> None:
        self.rows = rows
        self.cols = cols
        self.cursor_row = 0
        self.cursor_col = 0
        self._sgr: dict = self._default_sgr()
        self.grid: list[list[Cell]] = self._blank_grid(rows, cols)
        # DECSTBM scroll region (0-indexed, inclusive). Full screen until CSI r
        # (set_scroll_region) narrows it.
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        # History of lines scrolled off the top of the *main* screen only -- real
        # terminals don't add alt-screen (vim/less/htop) scrolling to scrollback, and
        # neither does DL/insert-delete-line, only a genuine top-of-screen scroll-up.
        # deque(maxlen=...) self-bounds memory instead of growing forever on a
        # long-running session or a `cat` of a huge file.
        self.scrollback: deque[list[Cell]] = deque(maxlen=scrollback_limit)
        # DECSC/DECRC (ESC 7/8) cursor save slot -- separate from the alt-screen's
        # own implicit save/restore below, matching real terminal behavior.
        self._dec_saved_cursor: tuple[int, int] | None = None
        # Alternate screen buffer (DECSET 47/1047/1049), used by vim/less/htop etc.
        self._alt_active = False
        self._saved_main_grid: list[list[Cell]] | None = None
        self._saved_main_cursor: tuple[int, int] = (0, 0)

    @staticmethod
    def _default_sgr() -> dict:
        return dict(fg=None, bg=None, bold=False, underline=False, reverse=False)

    @staticmethod
    def _blank_grid(rows: int, cols: int) -> list[list[Cell]]:
        return [[Cell() for _ in range(cols)] for _ in range(rows)]

    def _resized_grid(self, old_grid: list[list[Cell]], rows: int, cols: int) -> list[list[Cell]]:
        new_grid = self._blank_grid(rows, cols)
        for r in range(min(rows, len(old_grid))):
            for c in range(min(cols, len(old_grid[r]))):
                new_grid[r][c] = old_grid[r][c]
        return new_grid

    def resize(self, rows: int, cols: int) -> None:
        # Known simplification: scrollback rows keep whatever width they had when
        # they were captured and are never reflowed -- real terminals do this, it's
        # just not implemented here yet.
        self.grid = self._resized_grid(self.grid, rows, cols)
        if self._saved_main_grid is not None:
            self._saved_main_grid = self._resized_grid(self._saved_main_grid, rows, cols)
        self.rows, self.cols = rows, cols
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.cursor_row = min(self.cursor_row, rows - 1)
        self.cursor_col = min(self.cursor_col, cols - 1)

    # --- cursor save/restore (DECSC/DECRC, ESC 7/8) ---

    def save_cursor(self) -> None:
        self._dec_saved_cursor = (self.cursor_row, self.cursor_col)

    def restore_cursor(self) -> None:
        if self._dec_saved_cursor is not None:
            self.cursor_row, self.cursor_col = self._dec_saved_cursor

    # --- alternate screen buffer (DECSET 47/1047/1049) ---

    def enter_alt_screen(self) -> None:
        if self._alt_active:
            return
        self._alt_active = True
        self._saved_main_grid = self.grid
        self._saved_main_cursor = (self.cursor_row, self.cursor_col)
        self.grid = self._blank_grid(self.rows, self.cols)
        self.cursor_row = 0
        self.cursor_col = 0

    def exit_alt_screen(self) -> None:
        if not self._alt_active:
            return
        self._alt_active = False
        assert self._saved_main_grid is not None
        self.grid = self._saved_main_grid
        self._saved_main_grid = None
        self.cursor_row, self.cursor_col = self._saved_main_cursor

    # --- writing text ---

    def put_char(self, ch: str) -> None:
        if self.cursor_col >= self.cols:
            self.cursor_col = 0
            self.linefeed()
        self.grid[self.cursor_row][self.cursor_col] = Cell(char=ch, **self._sgr)
        self.cursor_col += 1

    def linefeed(self) -> None:
        if self.cursor_row == self.scroll_bottom:
            self._scroll_region_up(1)
        elif self.cursor_row < self.rows - 1:
            self.cursor_row += 1

    def index(self) -> None:
        """ESC D (IND): same cursor movement as linefeed, without a carriage return."""
        self.linefeed()

    def reverse_index(self) -> None:
        """ESC M (RI): move up, scrolling the region down if already at its top."""
        if self.cursor_row == self.scroll_top:
            self._scroll_region_down(1)
        elif self.cursor_row > 0:
            self.cursor_row -= 1

    def _scroll_region_up(self, n: int = 1) -> None:
        # Only a scroll that includes the real top of the (main) screen counts as
        # history -- an inner DECSTBM region scrolling (e.g. a program splitting the
        # screen) isn't "the terminal's history" in the way xterm et al. treat it,
        # and alt-screen apps (vim/less/htop) never contribute to scrollback at all.
        if not self._alt_active and self.scroll_top == 0:
            n = min(max(n, 0), self.scroll_bottom - self.scroll_top + 1)
            self.scrollback.extend(self.grid[self.scroll_top:self.scroll_top + n])
        self._shift_up(self.scroll_top, self.scroll_bottom, n)

    def _scroll_region_down(self, n: int = 1) -> None:
        self._shift_down(self.scroll_top, self.scroll_bottom, n)

    def _shift_up(self, from_row: int, to_row: int, n: int = 1) -> None:
        # Lines from_row..to_row move up by n, blanks fill in at to_row. n is
        # clamped to the region height -- params come straight from the parser
        # (attacker-controlled in principle), and an uncapped loop here would be a
        # real hang, not just a crash, unlike the plain int-parsing DoS fixed earlier.
        n = min(max(n, 0), to_row - from_row + 1)
        for _ in range(n):
            del self.grid[from_row]
            self.grid.insert(to_row, [Cell() for _ in range(self.cols)])

    def _shift_down(self, from_row: int, to_row: int, n: int = 1) -> None:
        n = min(max(n, 0), to_row - from_row + 1)
        for _ in range(n):
            del self.grid[to_row]
            self.grid.insert(from_row, [Cell() for _ in range(self.cols)])

    def set_scroll_region(self, top: int = 0, bottom: int = 0) -> None:
        """DECSTBM (CSI Pt;Pb r). top/bottom are 1-indexed; 0 means "unspecified"."""
        t = top - 1 if top > 0 else 0
        b = bottom - 1 if bottom > 0 else self.rows - 1
        if t < 0 or b >= self.rows or t >= b:
            return  # invalid region, ignored per real terminal behavior
        self.scroll_top = t
        self.scroll_bottom = b
        self.cursor_row = 0
        self.cursor_col = 0

    def insert_lines(self, n: int = 1) -> None:
        """IL (CSI Pn L): insert n blank lines at the cursor row, within the region."""
        if self.scroll_top <= self.cursor_row <= self.scroll_bottom:
            self._shift_down(self.cursor_row, self.scroll_bottom, n)

    def delete_lines(self, n: int = 1) -> None:
        """DL (CSI Pn M): delete n lines at the cursor row, within the region."""
        if self.scroll_top <= self.cursor_row <= self.scroll_bottom:
            self._shift_up(self.cursor_row, self.scroll_bottom, n)

    def insert_chars(self, n: int = 1) -> None:
        """ICH (CSI Pn @): insert n blank cells at the cursor, within the line."""
        row = self.grid[self.cursor_row]
        n = min(max(n, 0), self.cols - self.cursor_col)
        if n == 0:
            return
        row[self.cursor_col:self.cursor_col] = [Cell() for _ in range(n)]
        del row[self.cols:]

    def delete_chars(self, n: int = 1) -> None:
        """DCH (CSI Pn P): delete n cells at the cursor, within the line."""
        row = self.grid[self.cursor_row]
        del row[self.cursor_col:self.cursor_col + max(n, 0)]
        row.extend(Cell() for _ in range(self.cols - len(row)))

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
        elif mode == 2:
            self.grid = self._blank_grid(self.rows, self.cols)
        elif mode == 3:
            # xterm extension ("erase saved lines") -- what `clear` actually sends
            # to wipe scrollback along with the visible screen.
            self.grid = self._blank_grid(self.rows, self.cols)
            self.scrollback.clear()

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

    def scrollback_text(self) -> str:
        return "\n".join("".join(cell.char for cell in row).rstrip() for row in self.scrollback)
