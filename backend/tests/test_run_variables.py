import pytest

from app.run_variables import _apply_mode


def test_variable_write_modes() -> None:
    assert _apply_mode({"a": 1}, {"b": 2}, "MERGE") == {"a": 1, "b": 2}
    assert _apply_mode([1], [2, 3], "APPEND") == [1, 2, 3]
    assert _apply_mode(None, "first", "APPEND") == ["first"]
    assert _apply_mode(2, 3, "INCREMENT") == 5


def test_variable_mode_rejects_incompatible_values() -> None:
    with pytest.raises(ValueError, match="MERGE"):
        _apply_mode([], {"a": 1}, "MERGE")
    with pytest.raises(ValueError, match="INCREMENT"):
        _apply_mode(2, "three", "INCREMENT")
