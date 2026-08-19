"""In-memory VT screen buffer: a grid of cells, cursor, SGR/hyperlink attributes,
scrollback, private-mode tracking, and OSC metadata (title/palette/clipboard).

No rendering, no PTY awareness — a Screen is just a model that a Parser writes into.
The one exception is `write_back`: some real sequences (device/mode *queries*, not
just settings) require the terminal to respond by writing bytes back to the child --
`report_key_encoding_flags` (CSI ? u) is the first consumer, confirmed against kitty's
real `screen_report_key_encoding_flags` (`write_escape_code_to_child`). Future
query-style features (DA1/DA2 device attributes, DSR status reports) will reuse the
same `write_back` callable rather than inventing their own channel.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from puppy.graphics import GraphicsManager


@dataclass
class Cell:
    char: str = " "
    fg: int | tuple[int, int, int] | None = None
    bg: int | tuple[int, int, int] | None = None
    bold: bool = False
    underline: bool = False
    reverse: bool = False
    hyperlink: str | None = None


class Screen:
    _SGR_COLORS_FG = set(range(30, 38)) | set(range(90, 98))
    _SGR_COLORS_BG = set(range(40, 48)) | set(range(100, 108))

    def __init__(
        self,
        rows: int = 24,
        cols: int = 80,
        scrollback_limit: int = 2000,
        write_back: Callable[[bytes], None] | None = None,
    ) -> None:
        # No-op by default so every existing Screen()-with-no-PTY test/use
        # keeps working unchanged; real callers (puppy.render.app, __main__.py)
        # pass session.write.
        self._write_back: Callable[[bytes], None] = write_back or (lambda data: None)
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
        # DECSC/DECRC (ESC 7/8) cursor save slot. Confirmed against kitty's real
        # screen_toggle_screen_buffer (screen.c): entering/exiting the alternate
        # screen buffer via mode 1049 reuses this *same* slot (calls
        # screen_save_cursor/screen_restore_cursor directly) rather than keeping a
        # separate one -- so a DECSC done right before a 1049 entry gets clobbered
        # by it, same as on a real terminal.
        self._dec_saved_cursor: tuple[int, int] | None = None
        # Alternate screen buffer (DECSET 47/1047/1049), used by vim/less/htop etc.
        self._alt_active = False
        self._saved_main_grid: list[list[Cell]] | None = None
        # Generic DEC private mode tracking (bracketed paste 2004, focus reporting
        # 1004, synchronized output 2026, mouse modes, etc.) -- matches how kitty's
        # own SIMPLE_MODE macro just stores a bool per mode number. Modes that need
        # actual behavior (alt-screen) are handled as special cases in Parser before
        # falling through to this generic set; everything else is "recognized, state
        # tracked, no behavior yet" until something downstream needs it.
        self.private_modes: set[int] = set()
        # OSC family: window/icon title (0/1/2), 256-color palette overrides (4),
        # default fg/bg color (10/11), clipboard (52) -- all just metadata storage,
        # nothing reads/renders them yet. Values are kept as raw spec strings
        # (e.g. "rgb:ff/00/80" or "#ff0080") rather than parsed into RGB tuples --
        # that parsing belongs with whatever eventually renders them.
        self.window_title: str | None = None
        self.icon_title: str | None = None
        self.palette: dict[int, str] = {}
        self.default_fg_spec: str | None = None
        self.default_bg_spec: str | None = None
        self.clipboard: dict[str, str] = {}
        # OSC 8 hyperlinks: like SGR, an active link attaches to every subsequently
        # written Cell until cleared (OSC 8 with an empty URI).
        self._active_hyperlink: str | None = None
        # Kitty keyboard protocol progressive-enhancement flags (CSI = flags ; mode
        # u). Single flat value, not a real push/pop stack and not separated by
        # main/alt screen -- both are real gaps in kitty's actual model (confirmed
        # via its screen_push/pop_key_encoding_flags), deliberately not built yet.
        # See puppy.kitty_keyboard's module docstring for the full protocol scope.
        self.key_encoding_flags = 0
        # Kitty graphics protocol (model layer only -- see puppy.graphics for
        # scope). Screen.graphics_command is Parser's entry point (APC 'G'
        # commands); GraphicsManager owns the actual image/placement state.
        self.graphics = GraphicsManager()

    def graphics_command(self, control: dict[str, str], payload: bytes) -> None:
        self.graphics.handle_command(control, payload, self.cursor_row, self.cursor_col)

    def set_key_encoding_flags(self, val: int, how: int = 1) -> None:
        """how: 1=set directly, 2=OR into current, 3=AND-NOT (remove) from
        current -- confirmed exact against kitty's real
        screen_set_key_encoding_flags (screen.c)."""
        q = val & 0x7F
        if how == 1:
            self.key_encoding_flags = q
        elif how == 2:
            self.key_encoding_flags |= q
        elif how == 3:
            self.key_encoding_flags &= ~q

    def report_key_encoding_flags(self) -> None:
        """CSI ? u (query): responds with CSI ? <flags> u, exact format
        confirmed against kitty's real screen_report_key_encoding_flags."""
        self._write_back(f"\x1b[?{self.key_encoding_flags}u".encode("ascii"))

    def set_private_mode(self, mode: int, enabled: bool) -> None:
        if enabled:
            self.private_modes.add(mode)
        else:
            self.private_modes.discard(mode)

    @property
    def bracketed_paste(self) -> bool:
        return 2004 in self.private_modes

    @property
    def focus_tracking(self) -> bool:
        return 1004 in self.private_modes

    @property
    def sync_output_pending(self) -> bool:
        return 2026 in self.private_modes

    # --- OSC family ---

    def set_window_title(self, title: str) -> None:
        self.window_title = title

    def set_icon_title(self, title: str) -> None:
        self.icon_title = title

    def set_palette_color(self, index: int, spec: str) -> None:
        self.palette[index] = spec

    def set_default_fg(self, spec: str) -> None:
        self.default_fg_spec = spec

    def set_default_bg(self, spec: str) -> None:
        self.default_bg_spec = spec

    def set_clipboard(self, selection: str, data: str) -> None:
        self.clipboard[selection] = data

    def set_hyperlink(self, uri: str | None) -> None:
        self._active_hyperlink = uri

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

    def enter_alt_screen(self, save_cursor: bool = True) -> None:
        """save_cursor is only True for mode 1049; 47/1047 don't touch cursor state
        at all, per kitty's screen_toggle_screen_buffer -- the caller (Parser)
        decides which, based on which mode number was actually seen."""
        if self._alt_active:
            return
        self._alt_active = True
        self._saved_main_grid = self.grid
        if save_cursor:
            self.save_cursor()
        self.grid = self._blank_grid(self.rows, self.cols)
        self.cursor_row = 0
        self.cursor_col = 0

    def exit_alt_screen(self, save_cursor: bool = True) -> None:
        if not self._alt_active:
            return
        self._alt_active = False
        assert self._saved_main_grid is not None
        self.grid = self._saved_main_grid
        self._saved_main_grid = None
        if save_cursor:
            self.restore_cursor()

    # --- writing text ---

    def put_char(self, ch: str) -> None:
        if self.cursor_col >= self.cols:
            self.cursor_col = 0
            self.linefeed()
        self.grid[self.cursor_row][self.cursor_col] = Cell(
            char=ch, hyperlink=self._active_hyperlink, **self._sgr
        )
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
        """DECSTBM (CSI Pt;Pb r). top/bottom are 1-indexed; 0 means "unspecified".

        Ported from kitty's screen_set_margins (kitty/screen.c): out-of-range values
        are clamped to the screen, not rejected outright -- only checked afterwards
        for the ambiguous span == 1 row -- confirmed by reading kitty's real source
        rather than assumed, after "does this actually match kitty" came up.
        """
        top = top if top > 0 else 1
        bottom = bottom if bottom > 0 else self.rows
        top = min(self.rows, top)
        bottom = min(self.rows, bottom)
        t = top - 1
        b = bottom - 1
        if b <= t:
            return  # invalid/degenerate region, ignored per real terminal behavior
        self.scroll_top = t
        self.scroll_bottom = b
        self.cursor_row = 0
        self.cursor_col = 0

    def insert_lines(self, n: int = 1) -> None:
        """IL (CSI Pn L): insert n blank lines at the cursor row, within the region.

        Also does a carriage return, per kitty's real screen_insert_lines (screen.c)
        -- confirmed by reading the source, not assumed from the DEC/ECMA-48 spec
        text alone, which doesn't mention it. ICH/DCH do *not* do this, only the
        line-level ops.
        """
        if self.scroll_top <= self.cursor_row <= self.scroll_bottom:
            self._shift_down(self.cursor_row, self.scroll_bottom, n)
            self.carriage_return()

    def delete_lines(self, n: int = 1) -> None:
        """DL (CSI Pn M): delete n lines at the cursor row, within the region.

        Also does a carriage return, matching kitty's real screen_delete_lines.
        """
        if self.scroll_top <= self.cursor_row <= self.scroll_bottom:
            self._shift_up(self.cursor_row, self.scroll_bottom, n)
            self.carriage_return()

    def _bce_cell(self) -> Cell:
        """A blank cell filled with the *current* SGR state (back-color-erase).

        Confirmed against kitty's real line_apply_cursor (line.c): ED/EL/ICH/DCH all
        fill newly-blank cells with the cursor's full current SGR (fg/bg/bold/
        underline/reverse -- everything cursor_as_gpu_cell copies, not just bg
        despite the name "back-color-erase"), never the hyperlink. IL/DL and a full
        alt-screen/resize blank do NOT do this (confirmed via linebuf_insert_lines/
        linebuf_delete_lines/linebuf_clear, which all use a true zeroed blank) --
        that asymmetry is real, not an oversight, so don't "fix" it into consistency.
        """
        return Cell(**self._sgr)

    def insert_chars(self, n: int = 1) -> None:
        """ICH (CSI Pn @): insert n blank cells at the cursor, within the line."""
        row = self.grid[self.cursor_row]
        n = min(max(n, 0), self.cols - self.cursor_col)
        if n == 0:
            return
        row[self.cursor_col:self.cursor_col] = [self._bce_cell() for _ in range(n)]
        del row[self.cols:]

    def delete_chars(self, n: int = 1) -> None:
        """DCH (CSI Pn P): delete n cells at the cursor, within the line."""
        row = self.grid[self.cursor_row]
        del row[self.cursor_col:self.cursor_col + max(n, 0)]
        row.extend(self._bce_cell() for _ in range(self.cols - len(row)))

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
            self.grid = [[self._bce_cell() for _ in range(self.cols)] for _ in range(self.rows)]
        elif mode == 3:
            # xterm extension ("erase saved lines") -- what `clear` actually sends
            # to wipe scrollback along with the visible screen.
            self.grid = [[self._bce_cell() for _ in range(self.cols)] for _ in range(self.rows)]
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
            self.grid[row][c] = self._bce_cell()

    # --- SGR (colors/attributes) ---

    def sgr(self, params: list[int], is_subparam: list[bool] | None = None) -> None:
        if not params:
            params = [0]
        if is_subparam is None or len(is_subparam) != len(params):
            is_subparam = [False] * len(params)
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
                # Normalized to the same 0-15 index space the 256-color path
                # (38;5;n) uses, not stored as the raw 30-37/90-97 attribute
                # code -- confirmed against kitty's real cursor_from_sgr
                # (cursor.c): `case 30...37: fg = (attr-30)<<8|1`, same tag as
                # the 256-color path. Storing the raw code would make fg=35
                # ambiguous between "SGR magenta" and "256-color index 35"
                # (a real, different color) -- a genuine bug, not a style
                # choice, caught while building the renderer's color
                # resolution and needing an unambiguous 0-255 index meaning.
                self._sgr["fg"] = (p - 30) if p < 90 else (p - 90 + 8)
            elif p in self._SGR_COLORS_BG:
                self._sgr["bg"] = (p - 40) if p < 100 else (p - 100 + 8)
            elif p in (38, 48) and i + 1 < len(params):
                target = "fg" if p == 38 else "bg"
                mode = params[i + 1]
                if mode == 5 and i + 2 < len(params):
                    self._sgr[target] = params[i + 2]
                    i += 2
                elif mode == 2:
                    # Ported from kitty's real sub-param handling (vt-parser.c,
                    # select_graphic_rendition): collect every ':'-chained value
                    # that follows, rather than assuming a fixed field count. ITU
                    # T.416 puts an optional color-space id *before* r/g/b, so
                    # whatever was collected, the last 3 values are always r, g, b
                    # -- correct for both `38:2:r:g:b` and `38:2:<cs>:r:g:b`.
                    # Plain `;`-separated form isn't sub-param-tracked at all, so
                    # it falls back to the fixed classic 3-param read.
                    end = i + 2
                    while end < len(params) and is_subparam[end]:
                        end += 1
                    if end == i + 2:
                        end = min(i + 5, len(params))
                    components = params[i + 2:end]
                    if len(components) >= 3:
                        self._sgr[target] = tuple(components[-3:])
                        i = end - 1
            i += 1

    # --- debug ---

    def dump_text(self) -> str:
        return "\n".join("".join(cell.char for cell in row).rstrip() for row in self.grid)

    def scrollback_text(self) -> str:
        return "\n".join("".join(cell.char for cell in row).rstrip() for row in self.scrollback)
