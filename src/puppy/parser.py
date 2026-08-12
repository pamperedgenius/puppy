"""Byte-level VT/ANSI escape-sequence parser.

Feeds bytes in, dispatches decoded characters and control sequences onto a
"sink" object (normally a `Screen`) that exposes: put_char, carriage_return,
linefeed, backspace, tab, cursor_up/down/forward/back, cursor_position,
erase_in_display, erase_in_line, sgr.

Baseline VT100/ECMA-48/xterm subset only — see PROGRESS.md milestones for what's
still missing (alt screen, scroll regions, mouse, OSC contents, kitty extensions).
"""
from __future__ import annotations

ESC = 0x1B
BEL = 0x07


class Parser:
    GROUND, ESCAPE, CHARSET, CSI, OSC = range(5)

    def __init__(self, sink) -> None:
        self.sink = sink
        self.state = self.GROUND
        self._params: list[str] = [""]
        self._private = False
        self._utf8_bytes = bytearray()
        self._osc_pending_esc = False

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
            self._private = False
            return
        if ch == "]":
            self.state = self.OSC
            self._osc_pending_esc = False
            return
        if ch in "()*+":
            self.state = self.CHARSET
            return
        # DECSC/DECRC/RIS/etc. not yet implemented
        self.state = self.GROUND

    # --- CSI: collect params, dispatch on final byte ---

    def _csi(self, byte: int) -> None:
        ch = chr(byte)
        if ch == "?" and self._params == [""]:
            self._private = True
            return
        if ch.isdigit():
            self._params[-1] += ch
            return
        if ch == ";":
            self._params.append("")
            return
        if 0x40 <= byte <= 0x7E:
            self._dispatch_csi(ch)
            self.state = self.GROUND
            return
        # intermediate bytes not yet handled

    def _param(self, idx: int, default: int) -> int:
        if idx >= len(self._params) or not self._params[idx]:
            return default
        return int(self._params[idx])

    def _dispatch_csi(self, final: str) -> None:
        if self._private:
            return  # DEC private modes (DECSET/DECRST) recognized, not yet acted on
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
        elif final == "m":
            params = [int(p) if p else 0 for p in self._params]
            self.sink.sgr(params)
        # else: not yet implemented, silently ignored

    # --- OSC: buffer until ST (ESC \) or BEL, contents discarded for now ---

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
        # OSC payload bytes are discarded — title/clipboard/hyperlinks not yet implemented

    def _finish_osc(self) -> None:
        self._osc_pending_esc = False
        self.state = self.GROUND
