from pathlib import Path

from ui.fb0_service import _parse_mode_string, _read_visible_size


def test_parse_mode_string_accepts_progressive_and_interlaced_suffixes() -> None:
    assert _parse_mode_string("800x480") == (800, 480)
    assert _parse_mode_string("1920x1080i") == (1920, 1080)
    assert _parse_mode_string("1024x600p60") == (1024, 600)


def test_read_visible_size_prefers_connected_drm_mode(tmp_path: Path, monkeypatch) -> None:
    disconnected = tmp_path / "sys" / "class" / "drm" / "card1-HDMI-A-1"
    disconnected.mkdir(parents=True)
    (disconnected / "status").write_text("disconnected\n")

    connected = tmp_path / "sys" / "class" / "drm" / "card1-HDMI-A-2"
    connected.mkdir(parents=True)
    (connected / "status").write_text("connected\n")
    (connected / "modes").write_text("800x480\n1920x1080\n")

    real_path_class = Path

    monkeypatch.setattr("ui.fb0_service._read_virtual_size", lambda path: (1024, 768))

    def _fake_path(value: str | Path) -> Path:
        text = str(value)
        if text == "/sys/class/drm":
            return tmp_path / "sys" / "class" / "drm"
        if text.startswith("/sys/class/drm"):
            return real_path_class(str((tmp_path / "sys" / "class" / "drm") / text.removeprefix("/sys/class/drm/")))
        return real_path_class(value)

    monkeypatch.setattr("ui.fb0_service.Path", _fake_path)

    assert _read_visible_size(Path("/dev/fb0")) == (800, 480)
