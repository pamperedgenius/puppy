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


def test_put_action_referring_to_nonexistent_image_errors_not_crashes():
    gm = GraphicsManager()
    response = gm.handle_command({"a": "p", "i": "1"}, b"", cursor_row=0, cursor_col=0)
    assert gm.images == {}
    assert gm.placements == []
    assert response == b"\x1b_Gi=1;ENOENT:Put command refers to non-existent image with id: 1\x1b\\"


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


# --- command responses (OK/error, quiet gating) ---


def test_successful_transmit_and_display_responds_ok():
    gm = GraphicsManager()
    pixels = bytes(range(12))
    response = gm.handle_command({"a": "T", "f": "24", "s": "2", "v": "2", "i": "7"}, _b64(pixels), cursor_row=0, cursor_col=0)
    assert response == b"\x1b_Gi=7;OK\x1b\\"


def test_transmit_only_still_responds_ok():
    gm = GraphicsManager()
    pixels = bytes(range(12))
    response = gm.handle_command({"a": "t", "f": "24", "s": "2", "v": "2", "i": "9"}, _b64(pixels), cursor_row=0, cursor_col=0)
    assert response == b"\x1b_Gi=9;OK\x1b\\"


def test_failed_transmit_responds_with_error_code():
    gm = GraphicsManager()
    short = bytes(range(6))  # declares 2x2 RGB (needs 12 bytes)
    response = gm.handle_command({"a": "T", "f": "24", "s": "2", "v": "2", "i": "2"}, _b64(short), cursor_row=0, cursor_col=0)
    assert response == b"\x1b_Gi=2;EINVAL:Image dimensions 2x2 do not match data size 6\x1b\\"


def test_response_without_image_id_is_none():
    gm = GraphicsManager()
    # No 's'/'v' -> rejected before a load even starts, and no 'i' either.
    response = gm.handle_command({"a": "T", "f": "24"}, _b64(b""), cursor_row=0, cursor_col=0)
    assert response is None


def test_chunked_transmission_responds_only_after_final_chunk():
    gm = GraphicsManager()
    pixels = bytes(range(12))
    first, second = pixels[:6], pixels[6:]
    response = gm.handle_command(
        {"a": "T", "f": "24", "s": "2", "v": "2", "i": "5", "m": "1"}, _b64(first), cursor_row=0, cursor_col=0
    )
    assert response is None
    response = gm.handle_command({"m": "0"}, _b64(second), cursor_row=0, cursor_col=0)
    assert response == b"\x1b_Gi=5;OK\x1b\\"


def test_quiet_1_suppresses_ok_but_not_error():
    gm = GraphicsManager()
    pixels = bytes(range(12))
    ok_response = gm.handle_command(
        {"a": "T", "f": "24", "s": "2", "v": "2", "i": "1", "q": "1"}, _b64(pixels), cursor_row=0, cursor_col=0
    )
    assert ok_response is None
    short = bytes(range(6))
    error_response = gm.handle_command(
        {"a": "T", "f": "24", "s": "2", "v": "2", "i": "2", "q": "1"}, _b64(short), cursor_row=0, cursor_col=0
    )
    assert error_response is not None and b"EINVAL" in error_response


def test_quiet_2_suppresses_everything():
    gm = GraphicsManager()
    short = bytes(range(6))
    response = gm.handle_command(
        {"a": "T", "f": "24", "s": "2", "v": "2", "i": "2", "q": "2"}, _b64(short), cursor_row=0, cursor_col=0
    )
    assert response is None


# --- a=p (put) ---


def _transmit_only(gm: GraphicsManager, image_id: int, width: int, height: int) -> None:
    payload = bytes((x + y) % 256 for y in range(height) for x in range(width) for _ in range(3))
    gm.handle_command({"a": "t", "f": "24", "s": str(width), "v": str(height), "i": str(image_id)}, _b64(payload), cursor_row=0, cursor_col=0)


def test_put_displays_a_previously_transmitted_image():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    response = gm.handle_command({"a": "p", "i": "1"}, b"", cursor_row=3, cursor_col=4)
    assert response == b"\x1b_Gi=1;OK\x1b\\"
    assert len(gm.placements) == 1
    assert gm.placements[0].row == 3 and gm.placements[0].col == 4


def test_put_with_placement_id_and_z_index_and_explicit_cell_span():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 4, 4)
    response = gm.handle_command(
        {"a": "p", "i": "1", "p": "9", "z": "-5", "c": "2", "r": "3"}, b"", cursor_row=0, cursor_col=0
    )
    assert response == b"\x1b_Gi=1,p=9;OK\x1b\\"
    placement = gm.placements[0]
    assert placement.placement_id == 9
    assert placement.z_index == -5
    assert placement.num_cols == 2
    assert placement.num_rows == 3


def test_put_with_crop_offsets():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 10, 10)
    gm.handle_command({"a": "p", "i": "1", "x": "2", "y": "3", "w": "4", "h": "5"}, b"", cursor_row=0, cursor_col=0)
    placement = gm.placements[0]
    assert (placement.src_x, placement.src_y, placement.src_width, placement.src_height) == (2, 3, 4, 5)


def test_repeated_put_with_same_placement_id_replaces_it():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    gm.handle_command({"a": "p", "i": "1", "p": "9"}, b"", cursor_row=0, cursor_col=0)
    gm.handle_command({"a": "p", "i": "1", "p": "9"}, b"", cursor_row=5, cursor_col=6)
    assert len(gm.placements) == 1
    assert gm.placements[0].row == 5 and gm.placements[0].col == 6


def test_a_equals_T_display_honors_put_params_from_the_transmit_command():
    # a=T's display step reuses the exact same put logic as a=p -- confirmed
    # against kitty's own grman_handle_command (see graphics.py docstring).
    gm = GraphicsManager()
    payload = bytes((x + y) % 256 for y in range(4) for x in range(4) for _ in range(3))
    gm.handle_command(
        {"a": "T", "f": "24", "s": "4", "v": "4", "i": "1", "p": "3", "z": "7", "c": "1", "r": "1"},
        _b64(payload), cursor_row=0, cursor_col=0,
    )
    placement = gm.placements[0]
    assert placement.placement_id == 3
    assert placement.z_index == 7
    assert placement.num_cols == 1 and placement.num_rows == 1


def test_put_referring_to_id_only_addressing_not_number():
    gm = GraphicsManager()
    response = gm.handle_command({"a": "p"}, b"", cursor_row=0, cursor_col=0)
    assert response is None  # no image id given at all -- no response possible
    assert gm.placements == []


# --- a=q (query) ---


def test_query_valid_transmission_responds_ok_and_does_not_persist():
    gm = GraphicsManager()
    pixels = bytes(range(12))
    response = gm.handle_command({"a": "q", "f": "24", "s": "2", "v": "2", "i": "1"}, _b64(pixels), cursor_row=0, cursor_col=0)
    assert response == b"\x1b_Gi=1;OK\x1b\\"
    assert 1 not in gm.images
    assert gm.placements == []


def test_query_invalid_transmission_responds_with_error():
    gm = GraphicsManager()
    short = bytes(range(6))
    response = gm.handle_command({"a": "q", "f": "24", "s": "2", "v": "2", "i": "1"}, _b64(short), cursor_row=0, cursor_col=0)
    assert response is not None and b"EINVAL" in response
    assert 1 not in gm.images


def test_query_without_id_produces_no_response():
    gm = GraphicsManager()
    response = gm.handle_command({"a": "q", "f": "24", "s": "2", "v": "2"}, _b64(bytes(range(12))), cursor_row=0, cursor_col=0)
    assert response is None


# --- a=d (delete) ---


def test_delete_all_clears_placements_but_keeps_image_data():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    gm.handle_command({"a": "p", "i": "1"}, b"", cursor_row=0, cursor_col=0)
    response = gm.handle_command({"a": "d", "d": "a"}, b"", cursor_row=0, cursor_col=0)
    assert response is None  # delete never produces a command response
    assert gm.placements == []
    assert 1 in gm.images


def test_delete_all_uppercase_also_frees_image_data():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    gm.handle_command({"a": "p", "i": "1"}, b"", cursor_row=0, cursor_col=0)
    gm.handle_command({"a": "d", "d": "A"}, b"", cursor_row=0, cursor_col=0)
    assert gm.placements == []
    assert gm.images == {}


def test_delete_by_id_only_removes_that_images_placements():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    _transmit_only(gm, 2, 2, 2)
    gm.handle_command({"a": "p", "i": "1"}, b"", cursor_row=0, cursor_col=0)
    gm.handle_command({"a": "p", "i": "2"}, b"", cursor_row=1, cursor_col=1)
    gm.handle_command({"a": "d", "d": "i", "i": "1"}, b"", cursor_row=0, cursor_col=0)
    assert [pl.image_id for pl in gm.placements] == [2]
    assert 1 in gm.images  # lowercase 'i' does not free image data


def test_delete_by_id_and_placement_id_removes_only_that_placement():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    gm.handle_command({"a": "p", "i": "1", "p": "1"}, b"", cursor_row=0, cursor_col=0)
    gm.handle_command({"a": "p", "i": "1", "p": "2"}, b"", cursor_row=1, cursor_col=1)
    gm.handle_command({"a": "d", "d": "i", "i": "1", "p": "1"}, b"", cursor_row=0, cursor_col=0)
    assert [pl.placement_id for pl in gm.placements] == [2]


def test_delete_by_z_index():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    gm.handle_command({"a": "p", "i": "1", "z": "5"}, b"", cursor_row=0, cursor_col=0)
    gm.handle_command({"a": "p", "i": "1", "z": "9"}, b"", cursor_row=1, cursor_col=1)
    gm.handle_command({"a": "d", "d": "z", "z": "5"}, b"", cursor_row=0, cursor_col=0)
    assert [pl.z_index for pl in gm.placements] == [9]


def test_delete_by_cursor_position():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    gm.handle_command({"a": "p", "i": "1"}, b"", cursor_row=2, cursor_col=2)
    gm.handle_command({"a": "d", "d": "c"}, b"", cursor_row=2, cursor_col=2)
    assert gm.placements == []


def test_delete_by_column_and_row_with_explicit_span():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 8, 8)
    gm.handle_command({"a": "p", "i": "1", "c": "3", "r": "3"}, b"", cursor_row=5, cursor_col=5)
    # 1-indexed column/row inside the 3x3 span starting at (5,5)
    response = gm.handle_command({"a": "d", "d": "x", "x": "7"}, b"", cursor_row=0, cursor_col=0)
    assert response is None
    assert gm.placements == []


def test_delete_unrecognized_action_is_ignored_not_crashed():
    gm = GraphicsManager()
    _transmit_only(gm, 1, 2, 2)
    gm.handle_command({"a": "p", "i": "1"}, b"", cursor_row=0, cursor_col=0)
    gm.handle_command({"a": "d", "d": "n"}, b"", cursor_row=0, cursor_col=0)  # by-number, unsupported
    assert len(gm.placements) == 1


# --- a=f (animation frame load) / a=a (animation control) ---


def _solid_rgb(width: int, height: int, pixel: tuple[int, int, int]) -> bytes:
    return bytes(pixel) * (width * height)


def _solid_rgba(width: int, height: int, pixel: tuple[int, int, int, int]) -> bytes:
    return bytes(pixel) * (width * height)


def _root_image(gm: GraphicsManager, image_id: int, width: int, height: int, pixel: tuple[int, int, int]) -> None:
    gm.handle_command(
        {"a": "t", "f": "24", "s": str(width), "v": str(height), "i": str(image_id)},
        _b64(_solid_rgb(width, height, pixel)),
        cursor_row=0, cursor_col=0,
    )


def test_animation_frame_full_transmit_creates_a_new_frame_with_default_gap():
    gm = GraphicsManager()
    _root_image(gm, 1, 2, 2, (255, 0, 0))
    response = gm.handle_command(
        {"a": "f", "f": "24", "i": "1"}, _b64(_solid_rgb(2, 2, (0, 255, 0))), cursor_row=0, cursor_col=0,
    )
    assert response == b"\x1b_Gi=1;OK\x1b\\"
    image = gm.images[1]
    assert len(image.frames) == 1
    assert image.frames[0].gap == 40  # kitty's real DEFAULT_GAP
    assert image.frames[0].data == bytes((0, 255, 0, 255)) * 4  # normalized to RGBA
    # root frame itself is untouched
    assert image.data == _solid_rgb(2, 2, (255, 0, 0))


def test_animation_frame_gap_explicit_and_gapless():
    gm = GraphicsManager()
    _root_image(gm, 1, 2, 2, (0, 0, 0))
    gm.handle_command({"a": "f", "f": "24", "i": "1", "z": "75"}, _b64(_solid_rgb(2, 2, (1, 1, 1))), cursor_row=0, cursor_col=0)
    gm.handle_command({"a": "f", "f": "24", "i": "1", "z": "-1"}, _b64(_solid_rgb(2, 2, (2, 2, 2))), cursor_row=0, cursor_col=0)
    image = gm.images[1]
    assert image.frames[0].gap == 75
    assert image.frames[1].gap == 0  # negative == gapless, floored at 0


def test_animation_frame_partial_rect_composites_over_transparent_black_by_default():
    gm = GraphicsManager()
    _root_image(gm, 1, 4, 4, (10, 10, 10))
    # A 2x2 opaque blue rect at offset (1, 1) within a new 4x4 frame.
    response = gm.handle_command(
        {"a": "f", "f": "32", "s": "2", "v": "2", "x": "1", "y": "1", "i": "1"},
        _b64(_solid_rgba(2, 2, (0, 0, 255, 255))),
        cursor_row=0, cursor_col=0,
    )
    assert response == b"\x1b_Gi=1;OK\x1b\\"
    frame = gm.images[1].frames[0]
    import numpy as np
    arr = np.frombuffer(frame.data, dtype=np.uint8).reshape(4, 4, 4)
    assert list(arr[0, 0]) == [0, 0, 0, 0]  # outside the rect: default transparent black
    assert list(arr[1, 1]) == [0, 0, 255, 255]  # inside the rect
    assert list(arr[2, 2]) == [0, 0, 255, 255]
    assert list(arr[3, 3]) == [0, 0, 0, 0]


def test_animation_frame_solid_background_color_via_Y():
    gm = GraphicsManager()
    _root_image(gm, 1, 2, 2, (0, 0, 0))
    response = gm.handle_command(
        {"a": "f", "f": "24", "s": "1", "v": "1", "i": "1", "Y": "4278190335"},  # 0xff0000ff opaque red
        _b64(_solid_rgb(1, 1, (0, 255, 0))),
        cursor_row=0, cursor_col=0,
    )
    assert response == b"\x1b_Gi=1;OK\x1b\\"
    import numpy as np
    arr = np.frombuffer(gm.images[1].frames[0].data, dtype=np.uint8).reshape(2, 2, 4)
    assert list(arr[0, 0]) == [0, 255, 0, 255]  # the transmitted 1x1 opaque rect, at (0,0)
    assert list(arr[1, 1]) == [255, 0, 0, 255]  # background color fills the rest: opaque red


def test_animation_frame_base_ref_c_copies_a_previous_frames_data():
    gm = GraphicsManager()
    _root_image(gm, 1, 2, 2, (9, 9, 9))
    # c=1: base the new frame on the root frame's own data, then overlay a
    # 1x1 opaque pixel at (0, 0).
    gm.handle_command(
        {"a": "f", "f": "24", "s": "1", "v": "1", "i": "1", "c": "1"},
        _b64(_solid_rgb(1, 1, (255, 255, 255))),
        cursor_row=0, cursor_col=0,
    )
    import numpy as np
    arr = np.frombuffer(gm.images[1].frames[0].data, dtype=np.uint8).reshape(2, 2, 4)
    assert list(arr[0, 0]) == [255, 255, 255, 255]  # overwritten by the transmitted pixel
    assert list(arr[1, 1]) == [9, 9, 9, 255]  # carried over from the root frame (c=1)


def test_animation_frame_overwrite_composition_mode_X_1_ignores_alpha():
    gm = GraphicsManager()
    _root_image(gm, 1, 1, 1, (0, 0, 0))
    gm.handle_command(
        {"a": "f", "f": "32", "s": "1", "v": "1", "i": "1", "X": "1"},
        _b64(_solid_rgba(1, 1, (200, 100, 50, 0))),  # fully transparent, but X=1 means plain replace
        cursor_row=0, cursor_col=0,
    )
    assert gm.images[1].frames[0].data == bytes((200, 100, 50, 0))


def test_animation_frame_edit_existing_frame_via_r_updates_it_in_place():
    gm = GraphicsManager()
    _root_image(gm, 1, 2, 2, (0, 0, 0))
    gm.handle_command({"a": "f", "f": "24", "i": "1"}, _b64(_solid_rgb(2, 2, (1, 1, 1))), cursor_row=0, cursor_col=0)
    original_gap = gm.images[1].frames[0].gap
    response = gm.handle_command(
        {"a": "f", "f": "24", "s": "1", "v": "1", "i": "1", "r": "2", "X": "1"},
        _b64(_solid_rgb(1, 1, (250, 250, 250))),
        cursor_row=0, cursor_col=0,
    )
    assert response == b"\x1b_Gi=1;OK\x1b\\"
    image = gm.images[1]
    assert len(image.frames) == 1  # edited in place, not a new frame
    assert image.frames[0].gap == original_gap  # no z= given -> gap unchanged
    import numpy as np
    arr = np.frombuffer(image.frames[0].data, dtype=np.uint8).reshape(2, 2, 4)
    assert list(arr[0, 0]) == [250, 250, 250, 255]
    assert list(arr[1, 1]) == [1, 1, 1, 255]  # rest of the existing frame untouched


def test_animation_frame_unknown_image_id_responds_enoent():
    gm = GraphicsManager()
    response = gm.handle_command({"a": "f", "f": "24", "s": "1", "v": "1", "i": "99"}, _b64(_solid_rgb(1, 1, (0, 0, 0))), cursor_row=0, cursor_col=0)
    assert response is not None and b"ENOENT" in response


def test_animation_frame_bad_r_reference_responds_einval():
    gm = GraphicsManager()
    _root_image(gm, 1, 1, 1, (0, 0, 0))
    response = gm.handle_command(
        {"a": "f", "f": "24", "s": "1", "v": "1", "i": "1", "r": "5"}, _b64(_solid_rgb(1, 1, (0, 0, 0))), cursor_row=0, cursor_col=0,
    )
    assert response is not None and b"EINVAL" in response


def test_animation_control_c_jumps_to_a_specific_frame_client_driven():
    gm = GraphicsManager()
    _root_image(gm, 1, 1, 1, (0, 0, 0))
    gm.handle_command({"a": "f", "f": "24", "i": "1"}, _b64(_solid_rgb(1, 1, (1, 1, 1))), cursor_row=0, cursor_col=0)
    gm.handle_command({"a": "f", "f": "24", "i": "1"}, _b64(_solid_rgb(1, 1, (2, 2, 2))), cursor_row=0, cursor_col=0)
    response = gm.handle_command({"a": "a", "i": "1", "c": "3"}, b"", cursor_row=0, cursor_col=0)
    assert response == b"\x1b_Gi=1;OK\x1b\\"
    assert gm.images[1].current_frame_index == 2  # frame number 3 -> extra_frames[1]


def test_animation_control_gap_via_r_and_z_including_root_frame():
    gm = GraphicsManager()
    _root_image(gm, 1, 1, 1, (0, 0, 0))
    gm.handle_command({"a": "a", "i": "1", "r": "1", "z": "123"}, b"", cursor_row=0, cursor_col=0)
    assert gm.images[1].root_gap == 123  # the only way to give the root frame a nonzero gap


def test_animation_control_unknown_image_id_responds_enoent():
    gm = GraphicsManager()
    response = gm.handle_command({"a": "a", "i": "42", "s": "3"}, b"", cursor_row=0, cursor_col=0)
    assert response is not None and b"ENOENT" in response


def test_animation_control_state_start_sets_running_and_resets_loop_counter():
    gm = GraphicsManager()
    _root_image(gm, 1, 1, 1, (0, 0, 0))
    gm.handle_command({"a": "f", "f": "24", "i": "1"}, _b64(_solid_rgb(1, 1, (1, 1, 1))), cursor_row=0, cursor_col=0)
    gm.images[1].current_loop = 7
    gm.handle_command({"a": "a", "i": "1", "s": "3"}, b"", cursor_row=0, cursor_col=0)
    image = gm.images[1]
    assert image.animation_state == 2  # running
    assert image.current_loop == 0
    assert image.frame_shown_at > 0


def test_animation_control_loop_count_v_encodes_as_max_loops_minus_one():
    gm = GraphicsManager()
    _root_image(gm, 1, 1, 1, (0, 0, 0))
    gm.handle_command({"a": "a", "i": "1", "v": "3"}, b"", cursor_row=0, cursor_col=0)
    assert gm.images[1].max_loops == 2
    gm.handle_command({"a": "a", "i": "1", "v": "1"}, b"", cursor_row=0, cursor_col=0)
    assert gm.images[1].max_loops == 0  # v=1 -- infinite, kitty's own encoding


def _gap_key(gap: int) -> str:
    # z=0 means "use the default gap", not "gap of exactly zero" -- per spec
    # the only way to request a real zero/gapless frame is a negative value,
    # which floors to 0 (see test_animation_frame_gap_explicit_and_gapless).
    return "-1" if gap == 0 else str(gap)


def _animated_image(gm: GraphicsManager, image_id: int, gaps: list[int]) -> None:
    """A root frame plus one extra frame per gap in `gaps[1:]`, with the root
    frame's own gap set to gaps[0] (only settable via a=a r=1). Each frame is
    a distinct 1x1 solid color so tests can identify which is current."""
    _root_image(gm, image_id, 1, 1, (0, 0, 0))
    gm.handle_command({"a": "a", "i": str(image_id), "r": "1", "z": _gap_key(gaps[0])}, b"", cursor_row=0, cursor_col=0)
    for n, gap in enumerate(gaps[1:], start=1):
        gm.handle_command(
            {"a": "f", "f": "24", "i": str(image_id), "z": _gap_key(gap)},
            _b64(_solid_rgb(1, 1, (n, n, n))),
            cursor_row=0, cursor_col=0,
        )


def test_tick_advances_and_loops_when_state_is_running():
    gm = GraphicsManager()
    _animated_image(gm, 1, gaps=[10, 10, 10])  # root + 2 extra frames, 10ms apart
    gm.handle_command({"a": "a", "i": "1", "s": "3"}, b"", cursor_row=0, cursor_col=0)
    image = gm.images[1]
    t0 = image.frame_shown_at

    assert gm.tick(t0 + 0.005) is False  # gap not elapsed yet
    assert image.current_frame_index == 0

    assert gm.tick(t0 + 0.011) is True
    assert image.current_frame_index == 1

    assert gm.tick(t0 + 0.022) is True
    assert image.current_frame_index == 2

    assert gm.tick(t0 + 0.033) is True  # wraps back to root, one loop completed
    assert image.current_frame_index == 0
    assert image.current_loop == 1


def test_tick_stalls_at_the_end_when_loading_then_resumes_on_a_new_frame():
    gm = GraphicsManager()
    _animated_image(gm, 1, gaps=[10, 10])  # root + 1 extra frame
    gm.handle_command({"a": "a", "i": "1", "s": "2"}, b"", cursor_row=0, cursor_col=0)  # loading
    image = gm.images[1]
    t0 = image.frame_shown_at

    assert gm.tick(t0 + 0.011) is True
    assert image.current_frame_index == 1

    # Would wrap back to the root next, but s=2 (loading) freezes here instead.
    assert gm.tick(t0 + 0.100) is False
    assert image.current_frame_index == 1

    gm.handle_command({"a": "f", "f": "24", "i": "1", "z": "10"}, _b64(_solid_rgb(1, 1, (9, 9, 9))), cursor_row=0, cursor_col=0)
    assert gm.tick(t0 + 0.200) is True
    assert image.current_frame_index == 2  # the newly transmitted frame, not a wrap to root


def test_tick_respects_max_loops():
    # v=3 -> max_loops=2 -> 2 total passes through the sequence, confirmed
    # against kitty's real scan_active_animations: a wrap only actually
    # happens (current_frame_index reset to 0) while the *post-increment*
    # loop counter is still below max_loops -- once it reaches max_loops the
    # wrap is refused and current_frame_index freezes at its last value
    # (never resetting to the root frame for that final, refused wrap).
    gm = GraphicsManager()
    _animated_image(gm, 1, gaps=[10, 10])  # root + 1 extra frame
    gm.handle_command({"a": "a", "i": "1", "s": "3", "v": "3"}, b"", cursor_row=0, cursor_col=0)
    image = gm.images[1]
    t0 = image.frame_shown_at

    assert gm.tick(t0 + 0.011) is True
    assert image.current_frame_index == 1  # pass 1: last frame

    assert gm.tick(t0 + 0.022) is True  # successful wrap: pass 2 begins
    assert image.current_frame_index == 0
    assert image.current_loop == 1

    assert gm.tick(t0 + 0.033) is True
    assert image.current_frame_index == 1  # pass 2: last frame

    assert gm.tick(t0 + 0.044) is False  # 2nd wrap attempt hits max_loops -- refused, frozen here
    assert image.current_frame_index == 1


def test_tick_skips_gapless_frames_within_one_tick():
    gm = GraphicsManager()
    _animated_image(gm, 1, gaps=[10, 0, 20])  # frame 2 (index 1) is gapless
    gm.handle_command({"a": "a", "i": "1", "s": "3"}, b"", cursor_row=0, cursor_col=0)
    image = gm.images[1]
    t0 = image.frame_shown_at
    assert gm.tick(t0 + 0.011) is True
    assert image.current_frame_index == 2  # skipped straight past the gapless frame 1


def test_tick_does_nothing_when_every_frame_is_gapless():
    gm = GraphicsManager()
    _animated_image(gm, 1, gaps=[0, 0])
    gm.handle_command({"a": "a", "i": "1", "s": "3"}, b"", cursor_row=0, cursor_col=0)
    image = gm.images[1]
    assert gm.tick(image.frame_shown_at + 10.0) is False
    assert image.current_frame_index == 0


def test_tick_does_nothing_for_stopped_animation():
    gm = GraphicsManager()
    _animated_image(gm, 1, gaps=[10, 10])
    image = gm.images[1]
    assert gm.tick(image.frame_shown_at + 10.0) is False
    assert image.current_frame_index == 0
