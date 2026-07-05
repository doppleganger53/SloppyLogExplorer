from __future__ import annotations

from tools.validate_gps_map_runtime import _gps_runtime_options


def test_gps_runtime_options_preserve_scoped_bounds() -> None:
    options = _gps_runtime_options(
        color_column="Current(A)",
        scope_start_seconds=1.0,
        scope_end_seconds=4.0,
    )

    assert options.color_column == "Current(A)"
    assert options.scope_start_seconds == 1.0
    assert options.scope_end_seconds == 4.0
