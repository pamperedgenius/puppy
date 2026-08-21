import base64
import io
import zlib

import puppy.graphics as graphics_module
from puppy.graphics import MAX_IMAGE_DIMENSION, GraphicsManager

PIL = __import__("PIL.Image", fromlist=["Image"])


def _b64(data: bytes) -> bytes:
    return base64.b64encode(data)


def _real_png(width: int, height: int) -> bytes:
    # A real PNG, not a synthetic byte string -- exercises Pillow's actual
    # decoder, not just puppy's control-flow around a mocked one.
    im = PIL.new("RGBA", (width, height))
    for y in range(height):
        for x in range(width):
            im.putpixel((x, y), (x * 10 % 256, y * 10 % 256, 128, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), im.tobytes()


def test_single_chunk_rgb_transmit_and_display():
    gm = GraphicsManager()
    pixels = bytes(range(12))  # 2x2 RGB (2*2*3 = 12 bytes)
    gm.handle_command({"a": "T", "f": "24", "s": "2", "v": "2", "i": "7"}, _b64(pixels), cursor_row=3, cursor_col=5)
    assert gm.images[7].width == 2
    assert gm.images[7].height == 2
    assert gm.images[7].format == 24
    assert gm.images[7].data == pixels
    assert len(gm.placements) == 1
    assert gm.placements[0] == gm.placements[0].__class__(image_id=7, row=3, col=5)


def test_transmit_only_action_t_does_not_place():
    gm = GraphicsManager()
    pixels = bytes(range(12))
    gm.handle_command({"a": "t", "f": "24", "s": "2", "v": "2", "i": "9"}, _b64(pixels), cursor_row=0, cursor_col=0)
    assert 9 in gm.images
    assert gm.placements == []


def test_default_action_behaves_like_transmit_only():
    # confirmed via kitty's grman_handle_command: case 0 (action absent) is
    # grouped with case 't', not case 'T'
    gm = GraphicsManager()
    pixels = bytes(range(12))
    gm.handle_command({"f": "24", "s": "2", "v": "2", "i": "3"}, _b64(pixels), cursor_row=0, cursor_col=0)
    assert 3 in gm.images
    assert gm.placements == []


def test_rgba_format():
    gm = GraphicsManager()
    pixels = bytes(range(16))  # 2x2 RGBA (2*2*4 = 16 bytes)
    gm.handle_command({"a": "T", "f": "32", "s": "2", "v": "2", "i": "1"}, _b64(pixels), cursor_row=0, cursor_col=0)
    assert gm.images[1].format == 32
    assert gm.images[1].data == pixels


def test_chunked_transmission_reassembles_across_multiple_commands():
    gm = GraphicsManager()
    pixels = bytes(range(12))
    first, second = pixels[:6], pixels[6:]
    gm.handle_command(
        {"a": "T", "f": "24", "s": "2", "v": "2", "i": "5", "m": "1"}, _b64(first), cursor_row=1, cursor_col=1
    )
    assert 5 not in gm.images  # still loading, more chunks expected
    gm.handle_command({"m": "0"}, _b64(second), cursor_row=1, cursor_col=1)
    assert gm.images[5].data == pixels
    assert len(gm.placements) == 1


def test_chunked_transmission_continuation_ignores_repeated_control_keys():
    # A real client shouldn't repeat width/height/format on continuation chunks,
    # but even if control data is present it must be ignored -- only the
    # in-progress load (set up on the first chunk) governs.
    gm = GraphicsManager()
    pixels = bytes(range(12))
    first, second = pixels[:6], pixels[6:]
    gm.handle_command(
        {"a": "T", "f": "24", "s": "2", "v": "2", "i": "5", "m": "1"}, _b64(first), cursor_row=0, cursor_col=0
    )
    gm.handle_command({"a": "T", "f": "32", "s": "99", "m": "0"}, _b64(second), cursor_row=0, cursor_col=0)
    assert gm.images[5].format == 24
    assert gm.images[5].width == 2


def test_short_transmission_is_discarded_not_stored():
    gm = GraphicsManager()
    short = bytes(range(6))  # declares 2x2 RGB (needs 12 bytes) but only sends 6
    gm.handle_command({"a": "T", "f": "24", "s": "2", "v": "2", "i": "2"}, _b64(short), cursor_row=0, cursor_col=0)
    assert 2 not in gm.images
    assert gm.placements == []


def test_overlong_chunk_beyond_declared_size_is_rejected():
    gm = GraphicsManager()
    too_much = bytes(range(20))  # declares 2x2 RGB (12 bytes) but sends 20
    gm.handle_command(
        {"a": "T", "f": "24", "s": "2", "v": "2", "i": "4"}, _b64(too_much), cursor_row=0, cursor_col=0
    )
    assert 4 not in gm.images
    # loading state was cleared, not left stuck -- a subsequent well-formed
    # command must succeed rather than being (wrongly) treated as a continuation
    pixels = bytes(range(12))
    gm.handle_command({"a": "T", "f": "24", "s": "2", "v": "2", "i": "8"}, _b64(pixels), cursor_row=0, cursor_col=0)
    assert gm.images[8].data == pixels


def test_oversized_dimensions_rejected():
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "24", "s": str(MAX_IMAGE_DIMENSION + 1), "v": "1", "i": "1"},
        _b64(b"\x00" * 30),
        cursor_row=0,
        cursor_col=0,
    )
    assert 1 not in gm.images


def test_zero_dimensions_rejected():
    gm = GraphicsManager()
    gm.handle_command({"a": "T", "f": "24", "s": "0", "v": "5", "i": "1"}, _b64(b""), cursor_row=0, cursor_col=0)
    assert 1 not in gm.images


def test_corrupt_png_data_discarded_not_stored():
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "100", "s": "2", "v": "2", "i": "1"}, _b64(b"not a real png"), cursor_row=0, cursor_col=0
    )
    assert 1 not in gm.images
    assert gm.placements == []


def test_non_direct_transmission_type_out_of_scope_ignored():
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "t": "f", "f": "24", "s": "2", "v": "2", "i": "1"}, b"/tmp/some/file", cursor_row=0, cursor_col=0
    )
    assert 1 not in gm.images


def test_put_action_out_of_scope_ignored_not_crashed():
    gm = GraphicsManager()
    gm.handle_command({"a": "p", "i": "1"}, b"", cursor_row=0, cursor_col=0)
    assert gm.images == {}
    assert gm.placements == []


def test_malformed_base64_payload_does_not_crash():
    gm = GraphicsManager()
    gm.handle_command({"a": "T", "f": "24", "s": "2", "v": "2", "i": "1"}, b"!!!not base64!!!", cursor_row=0, cursor_col=0)
    assert 1 not in gm.images


def test_png_decodes_real_image_and_overrides_declared_dimensions():
    # Confirmed against kitty's real inflate_png: the PNG's own header size
    # wins over whatever s/v the client declared -- deliberately send wrong
    # ones here to prove puppy doesn't just trust them.
    png_bytes, raw_rgba = _real_png(3, 2)
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "100", "s": "99", "v": "99", "i": "1"}, _b64(png_bytes), cursor_row=4, cursor_col=2
    )
    assert gm.images[1].width == 3
    assert gm.images[1].height == 2
    assert gm.images[1].format == 32  # PNG always decodes to RGBA, matching kitty's own libpng normalization
    assert gm.images[1].data == raw_rgba
    assert gm.placements[0].row == 4 and gm.placements[0].col == 2


def test_png_without_declared_dimensions_works():
    png_bytes, raw_rgba = _real_png(2, 4)
    gm = GraphicsManager()
    gm.handle_command({"a": "T", "f": "100", "i": "1"}, _b64(png_bytes), cursor_row=0, cursor_col=0)
    assert gm.images[1].width == 2
    assert gm.images[1].height == 4
    assert gm.images[1].data == raw_rgba


def test_png_chunked_transmission_reassembles():
    png_bytes, raw_rgba = _real_png(4, 4)
    mid = len(png_bytes) // 2
    first, second = png_bytes[:mid], png_bytes[mid:]
    gm = GraphicsManager()
    gm.handle_command({"a": "T", "f": "100", "i": "1", "m": "1"}, _b64(first), cursor_row=0, cursor_col=0)
    assert 1 not in gm.images
    gm.handle_command({"m": "0"}, _b64(second), cursor_row=0, cursor_col=0)
    assert gm.images[1].data == raw_rgba


def test_compressed_rgba_transmission_round_trips():
    pixels = bytes(range(16))  # 2x2 RGBA
    compressed = zlib.compress(pixels)
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "32", "s": "2", "v": "2", "o": "z", "i": "1"}, _b64(compressed), cursor_row=0, cursor_col=0
    )
    assert gm.images[1].data == pixels
    assert gm.images[1].format == 32


def test_compressed_png_round_trips():
    png_bytes, raw_rgba = _real_png(2, 2)
    compressed = zlib.compress(png_bytes)
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "100", "o": "z", "i": "1"}, _b64(compressed), cursor_row=0, cursor_col=0
    )
    assert gm.images[1].data == raw_rgba


def test_corrupt_compressed_data_discarded_not_stored():
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "32", "s": "2", "v": "2", "o": "z", "i": "1"},
        _b64(b"not really zlib data"),
        cursor_row=0,
        cursor_col=0,
    )
    assert 1 not in gm.images


def test_compressed_data_decompressing_to_wrong_size_discarded():
    # Valid zlib stream, but the decompressed length doesn't match the
    # declared width*height*bpp -- must be treated the same as a real
    # short/oversized uncompressed transmission, not stored.
    wrong_size_pixels = bytes(range(8))  # declares 2x2 RGBA (needs 16 bytes)
    compressed = zlib.compress(wrong_size_pixels)
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "32", "s": "2", "v": "2", "o": "z", "i": "1"}, _b64(compressed), cursor_row=0, cursor_col=0
    )
    assert 1 not in gm.images


def test_unknown_compression_value_ignored():
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "24", "s": "2", "v": "2", "o": "bogus", "i": "1"},
        _b64(bytes(range(12))),
        cursor_row=0,
        cursor_col=0,
    )
    assert gm.images == {}


def test_compressed_payload_exceeding_max_data_sz_is_rejected(monkeypatch):
    # Real DoS protection for the PNG/compressed path, which has no exact
    # upfront expected size -- confirmed against kitty's own MAX_DATA_SZ
    # (graphics.c). Patch it small so the test doesn't need to push 400MB.
    monkeypatch.setattr(graphics_module, "MAX_DATA_SZ", 8)
    gm = GraphicsManager()
    gm.handle_command(
        {"a": "T", "f": "32", "s": "2", "v": "2", "o": "z", "i": "1"},
        _b64(b"0123456789"),  # 10 raw bytes > patched 8-byte cap
        cursor_row=0,
        cursor_col=0,
    )
    assert 1 not in gm.images
    # loading state cleared, not stuck -- a subsequent well-formed command still works
    pixels = bytes(range(12))
    gm.handle_command({"a": "T", "f": "24", "s": "2", "v": "2", "i": "2"}, _b64(pixels), cursor_row=0, cursor_col=0)
    assert gm.images[2].data == pixels


def test_oversized_png_dimensions_rejected(monkeypatch):
    png_bytes, _ = _real_png(4, 4)
    monkeypatch.setattr(graphics_module, "MAX_IMAGE_DIMENSION", 3)  # smaller than the real 4x4 PNG above
    gm = GraphicsManager()
    gm.handle_command({"a": "T", "f": "100", "i": "1"}, _b64(png_bytes), cursor_row=0, cursor_col=0)
    assert 1 not in gm.images
