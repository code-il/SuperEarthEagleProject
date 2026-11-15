import os

import pytest

from backend.data_acquisition import cache


def test_atomic_write_retries_windows_sharing_violation(tmp_path, monkeypatch):
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(cache.os, "replace", flaky)
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)
    cache.atomic_write_json(tmp_path / "a.json", {"x": 1})
    assert (tmp_path / "a.json").read_text().strip().startswith("{")
    assert calls["n"] == 3


def test_atomic_write_gives_up_and_cleans_temp(tmp_path, monkeypatch):
    def always(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(cache.os, "replace", always)
    monkeypatch.setattr(cache.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        cache.atomic_write_json(tmp_path / "b.json", {"x": 1})
    assert not list(tmp_path.glob("*.part"))
