import base64

from puppy.graphics import MAX_IMAGE_DIMENSION, GraphicsManager


def _b64(data: bytes) -> bytes:
    return base64.b64encode(data)


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


def test_png_format_out_of_scope_ignored():
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
