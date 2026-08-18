"""Byte-level VT/ANSI escape-sequence parser.

Feeds bytes in, dispatches decoded characters and control sequences onto a "sink"
object (normally a `Screen`) that exposes: put_char, carriage_return, linefeed,
backspace, tab, cursor movement/erase/scroll-region/insert-delete methods, sgr,
save/restore_cursor, index/reverse_index, enter/exit_alt_screen, set_private_mode,
set_window_title/set_icon_title, set_palette_color, set_default_fg/bg,
set_clipboard, set_hyperlink.

Baseline VT100/ECMA-48/xterm subset plus the OSC family (title/palette/clipboard/
hyperlinks) and generic DEC private-mode tracking -- see PROGRESS.md milestones for
what's still missing (kitty keyboard/graphics protocols, mouse *event generation*
as opposed to mode tracking, Sixel).
"""
from __future__ import annotations

ESC = 0x1B
BEL = 0x07


class Parser:
    GROUND, ESCAPE, CHARSET, CSI, OSC = range(5)

    # Caps on CSI parameter accumulation. A malicious/corrupt byte stream (an
    # untrusted file, a compromised remote program's output) could otherwise send an
    # unbounded run of digits or ';'s; without a cap, int() on a long enough digit
    # string raises ValueError (Python's int-string-conversion limit, PEP 3.11+) and
    # crashes the whole parser on a single byte. 7 digits (max ~9999999) and 32 params
    # comfortably cover every real sequence (truecolor SGR needs 3-digit r/g/b) while
    # keeping worst-case cost bounded.
    _MAX_PARAM_LEN = 7
    _MAX_PARAMS = 32

    # OSC payloads (title, hyperlinks, clipboard base64, ...) are unbounded in the
    # spec, so they get buffered rather than parsed byte-by-byte -- same class of
    # risk as the CSI param DoS above, so capped from the start this time instead of
    # bolted on after the fact. 8192 bytes covers any real title/URI and a
    # reasonably-sized clipboard paste; kitty's own real limit wasn't found in a
    # quick source search, so this is a deliberately chosen defensive bound, not a
    # matched constant.
    _MAX_OSC_LEN = 8192

    def __init__(self, sink) -> None:
        self.sink = sink
        self.state = self.GROUND
        self._params: list[str] = [""]
        # Parallel to _params: whether each entry was introduced by ':' (an ITU
        # T.416 sub-parameter of the previous entry) rather than ';' (a new
        # top-level parameter). Matches kitty's own is_sub_param tracking -- needed
        # to tell `38:2:r:g:b` apart from the rarer `38:2:<colorspace>:r:g:b`
        # without guessing a fixed field count.
        self._is_subparam: list[bool] = [False]
        self._private = False
        # CSI = flags ; mode u (kitty keyboard protocol progressive enhancement).
        # A distinct prefix from '?' (DECSET/DECRST) -- confirmed against kitty's
        # real vt-parser.c: `case 'u': ... if (start_modifier == '=') ... else if
        # (start_modifier == '?') ...` are different branches. `CSI ? u` (query,
        # needs writing a response back to the child) and `CSI > u`/`CSI < u`
        # (push/pop a flags stack) are real, documented gaps -- not implemented,
        # see puppy.kitty_keyboard's module docstring.
        self._equals_prefix = False
        self._utf8_bytes = bytearray()
        self._osc_pending_esc = False
        self._osc_buf = bytearray()

    def feed(self, data: bytes) -> None:
        for byte in data:
            self._feed_byte(byte)

    def _feed_byte(self, byte: int) -> None:
        if self.state == self.GROUND:
            self._ground(byte)
        elif self.state == self.ESCAPE:
            self._escape(byte)
        elif self.state == self.CHARSET:
            self.state = self.GROUND  # charset designation not implemented, just consume
        elif self.state == self.CSI:
            self._csi(byte)
        elif self.state == self.OSC:
            self._osc(byte)

    # --- GROUND: plain text + C0 controls ---

    def _ground(self, byte: int) -> None:
        if byte == ESC:
            self.state = self.ESCAPE
            return
        if byte == 0x08:
            self.sink.backspace()
        elif byte == 0x09:
            self.sink.tab()
        elif byte in (0x0A, 0x0B, 0x0C):
            self.sink.linefeed()
        elif byte == 0x0D:
            self.sink.carriage_return()
        elif byte < 0x20 or byte == 0x7F:
            pass  # other C0/DEL controls not yet handled
        else:
            self._utf8_bytes.append(byte)
            self._try_decode_utf8()

    def _try_decode_utf8(self) -> None:
        try:
            ch = bytes(self._utf8_bytes).decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.reason == "unexpected end of data":
                return  # wait for more continuation bytes
            self._utf8_bytes.clear()  # invalid sequence, drop it
            return
        self._utf8_bytes.clear()
        self.sink.put_char(ch)

    # --- ESCAPE: one byte after ESC decides where we go ---

    def _escape(self, byte: int) -> None:
        ch = chr(byte)
        if ch == "[":
            self.state = self.CSI
            self._params = [""]
            self._is_subparam = [False]
            self._private = False
            self._equals_prefix = False
            return
        if ch == "]":
            self.state = self.OSC
            self._osc_pending_esc = False
            self._osc_buf = bytearray()
            return
        if ch in "()*+":
            self.state = self.CHARSET
            return
        if ch == "7":
            self.sink.save_cursor()
        elif ch == "8":
            self.sink.restore_cursor()
        elif ch == "D":
            self.sink.index()
        elif ch == "M":
            self.sink.reverse_index()
        # RIS and other single-char ESC sequences not yet implemented
        self.state = self.GROUND

    # --- CSI: collect params, dispatch on final byte ---

    def _csi(self, byte: int) -> None:
        ch = chr(byte)
        if ch == "?" and self._params == [""]:
            self._private = True
            return
        if ch == "=" and self._params == [""]:
            self._equals_prefix = True
            return
        if ch.isdigit():
            if len(self._params[-1]) < self._MAX_PARAM_LEN:
                self._params[-1] += ch
            return
        if ch in (";", ":"):
            # ':' is the ITU T.416 sub-parameter separator (used by modern truecolor
            # SGR like `38:2:r:g:b`, which is what kitty itself emits). Both land in
            # the same flat params list, but _is_subparam records which separator
            # introduced each entry -- Screen.sgr uses that to tell a plain 3-field
            # `38:2:r:g:b` apart from the rarer `38:2:<colorspace>:r:g:b`, the way
            # kitty's own parser does, instead of assuming a fixed field count.
            if len(self._params) < self._MAX_PARAMS:
                self._params.append("")
                self._is_subparam.append(ch == ":")
            return
        if 0x40 <= byte <= 0x7E:
            self._dispatch_csi(ch)
            self.state = self.GROUND
            return
        # intermediate bytes not yet handled

    def _param(self, idx: int, default: int) -> int:
        if idx >= len(self._params) or not self._params[idx]:
            return default
        try:
            return int(self._params[idx])
        except ValueError:
            return default

    # 47/1047/1049 (alternate screen buffer) are the only private modes with real
    # behavior wired up so far -- everything else DEC private (bracketed paste 2004,
    # focus reporting 1004, sync output 2026, mouse 1000+, DECOM, DECAWM, ...) just
    # gets its on/off state tracked generically via Screen.private_modes.
    _ALT_SCREEN_MODES = frozenset({47, 1047, 1049})

    def _dispatch_csi(self, final: str) -> None:
        if self._equals_prefix:
            if final == "u":
                self.sink.set_key_encoding_flags(self._param(0, 0), self._param(1, 1))
            return
        if self._private:
            self._dispatch_private_mode(final)
            return
        if final == "A":
            self.sink.cursor_up(self._param(0, 1))
        elif final == "B":
            self.sink.cursor_down(self._param(0, 1))
        elif final == "C":
            self.sink.cursor_forward(self._param(0, 1))
        elif final == "D":
            self.sink.cursor_back(self._param(0, 1))
        elif final in ("H", "f"):
            self.sink.cursor_position(self._param(0, 1), self._param(1, 1))
        elif final == "J":
            self.sink.erase_in_display(self._param(0, 0))
        elif final == "K":
            self.sink.erase_in_line(self._param(0, 0))
        elif final == "r":
            self.sink.set_scroll_region(self._param(0, 0), self._param(1, 0))
        elif final == "L":
            self.sink.insert_lines(self._param(0, 1))
        elif final == "M":
            self.sink.delete_lines(self._param(0, 1))
        elif final == "@":
            self.sink.insert_chars(self._param(0, 1))
        elif final == "P":
            self.sink.delete_chars(self._param(0, 1))
        elif final == "m":
            params = []
            for p in self._params:
                try:
                    params.append(int(p) if p else 0)
                except ValueError:
                    params.append(0)
            self.sink.sgr(params, self._is_subparam)
        # else: not yet implemented, silently ignored

    def _dispatch_private_mode(self, final: str) -> None:
        if final not in ("h", "l"):
            return  # only DECSET/DECRST handled so far
        enable = final == "h"
        for raw in self._params:
            if not raw:
                continue
            mode = int(raw)
            if mode in self._ALT_SCREEN_MODES:
                save_cursor = mode == 1049  # 47/1047 don't touch cursor state at all
                if enable:
                    self.sink.enter_alt_screen(save_cursor)
                else:
                    self.sink.exit_alt_screen(save_cursor)
            else:
                # Generic tracking for everything else (bracketed paste 2004, focus
                # reporting 1004, synchronized output 2026, mouse modes, DECOM,
                # DECAWM, ...) -- state recorded, no behavior wired up yet beyond
                # what Screen.private_modes exposes.
                self.sink.set_private_mode(mode, enable)

    # --- OSC: buffer until ST (ESC \) or BEL, then parse Ps;Pt ---

    def _osc(self, byte: int) -> None:
        if byte == BEL:
            self._finish_osc()
            return
        if byte == ESC:
            self._osc_pending_esc = True
            return
        if self._osc_pending_esc:
            self._osc_pending_esc = False
            if byte == ord("\\"):
                self._finish_osc()
                return
        if len(self._osc_buf) < self._MAX_OSC_LEN:
            self._osc_buf.append(byte)
        # else: silently truncated rather than growing unboundedly

    def _finish_osc(self) -> None:
        self._osc_pending_esc = False
        self.state = self.GROUND
        self._dispatch_osc(bytes(self._osc_buf))
        self._osc_buf = bytearray()

    @staticmethod
    def _safe_int(s: str, max_len: int = 6) -> int | None:
        # Same guard as CSI params: a long enough digit run inside the (already
        # length-capped) OSC buffer could still exceed Python's int-string
        # conversion limit on its own. max_len=6 is far more than any real OSC
        # code/index needs.
        if not s or len(s) > max_len or not s.isdigit():
            return None
        try:
            return int(s)
        except ValueError:
            return None

    def _dispatch_osc(self, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return  # malformed payload (or truncated mid-codepoint) -- ignore
        code_str, _, rest = text.partition(";")
        code = self._safe_int(code_str)
        if code is None:
            return
        if code == 0:
            self.sink.set_icon_title(rest)
            self.sink.set_window_title(rest)
        elif code == 1:
            self.sink.set_icon_title(rest)
        elif code == 2:
            self.sink.set_window_title(rest)
        elif code == 4:
            fields = rest.split(";")
            for i in range(0, len(fields) - 1, 2):
                index = self._safe_int(fields[i])
                if index is not None:
                    self.sink.set_palette_color(index, fields[i + 1])
        elif code == 10:
            self.sink.set_default_fg(rest)
        elif code == 11:
            self.sink.set_default_bg(rest)
        elif code == 52:
            selection, _, data = rest.partition(";")
            self.sink.set_clipboard(selection or "c", data)
        elif code == 8:
            _params, _, uri = rest.partition(";")
            self.sink.set_hyperlink(uri or None)
        # else: not yet implemented, silently ignored
