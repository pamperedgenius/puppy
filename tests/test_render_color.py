from puppy.render.color import srgb_color, srgb_to_linear


def test_srgb_to_linear_endpoints():
    assert srgb_to_linear(0) == 0.0
    assert srgb_to_linear(255) == 1.0


def test_srgb_to_linear_known_values():
    # These exact values were confirmed by round-tripping clear+readback
    # against the real GPU (see PROGRESS.md) -- not just the formula in
    # isolation, the actual hardware behavior.
    assert abs(srgb_to_linear(64) - 0.0513) < 0.001
    assert abs(srgb_to_linear(128) - 0.2159) < 0.001
    assert abs(srgb_to_linear(187) - 0.4969) < 0.001


def test_srgb_to_linear_is_monotonic():
    values = [srgb_to_linear(b) for b in range(0, 256, 17)]
    assert values == sorted(values)


def test_srgb_color_alpha_is_not_gamma_decoded():
    r, g, b, a = srgb_color(255, 0, 0, 128)
    assert a == 128 / 255.0  # linear passthrough, not srgb_to_linear(128)


def test_srgb_color_full_tuple():
    color = srgb_color(0, 128, 255)
    assert color[0] == 0.0
    assert abs(color[1] - 0.2159) < 0.001
    assert color[2] == 1.0
    assert color[3] == 1.0
