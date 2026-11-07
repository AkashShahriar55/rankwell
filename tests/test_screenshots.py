from pathlib import Path

import pytest
from shot_files import jpeg, png

from rankwell import screenshots
from rankwell.errors import RankwellError


@pytest.mark.parametrize(("size", "expected"), [
    ((1320, 2868), ["APP_IPHONE_67"]),
    ((2868, 1320), ["APP_IPHONE_67"]),
    ((1290, 2796), ["APP_IPHONE_67"]),
    ((1242, 2688), ["APP_IPHONE_65"]),
    ((1284, 2778), ["APP_IPHONE_65"]),
    ((1206, 2622), ["APP_IPHONE_61"]),
    ((1242, 2208), ["APP_IPHONE_55"]),
    ((1136, 600), ["APP_IPHONE_40"]),
    ((2064, 2752), ["APP_IPAD_PRO_3GEN_129"]),
    ((2048, 2732), ["APP_IPAD_PRO_3GEN_129", "APP_IPAD_PRO_129"]),
    ((1668, 2388), ["APP_IPAD_PRO_3GEN_11"]),
    ((1000, 2000), []),
    ((1096, 640), []),  # the 4" landscape size without status bar is 1136×600, not the portrait one turned
])
def test_size_to_display_type(size: tuple[int, int], expected: list[str]) -> None:
    assert screenshots.display_types(*size) == expected


def test_display_folder_names() -> None:
    assert screenshots.display_name("APP_IPHONE_67") == "APP_IPHONE_67"
    assert screenshots.display_name("iphone_65") == "APP_IPHONE_65"
    assert screenshots.display_name("ipad-pro-3gen-129") == "APP_IPAD_PRO_3GEN_129"
    assert screenshots.display_name("Any") == "any"
    assert screenshots.display_name("en-US") is None


def test_png_header_and_alpha(tmp_path: Path) -> None:
    rgb = screenshots.image_info(png(tmp_path / "a.png", 1320, 2868))
    assert rgb == {"format": "png", "width": 1320, "height": 2868, "alpha": False}
    assert screenshots.image_info(png(tmp_path / "b.png", 10, 20, color=6))["alpha"] is True
    assert screenshots.image_info(png(tmp_path / "c.png", 10, 20, color=4))["alpha"] is True
    assert screenshots.image_info(png(tmp_path / "d.png", 10, 20, color=3, trns=True))["alpha"] is True
    assert screenshots.image_info(png(tmp_path / "e.png", 10, 20, color=3))["alpha"] is False


def test_jpeg_header(tmp_path: Path) -> None:
    assert screenshots.image_info(jpeg(tmp_path / "a.jpg", 1242, 2688)) == {
        "format": "jpeg", "width": 1242, "height": 2688, "alpha": False}
    (tmp_path / "bad.png").write_bytes(b"not an image")
    with pytest.raises(ValueError):
        screenshots.image_info(tmp_path / "bad.png")


def test_scan_locale_and_display_folders(tmp_path: Path) -> None:
    png(tmp_path / "en-US" / "APP_IPHONE_67" / "10-last.png", 1320, 2868)
    png(tmp_path / "en-US" / "APP_IPHONE_67" / "2-second.png", 1320, 2868)
    png(tmp_path / "en-US" / "APP_IPHONE_67" / "1-first.png", 1290, 2796)
    png(tmp_path / "en-US" / "any" / "ipad.png", 2048, 2732)
    jpeg(tmp_path / "de-DE" / "01.jpg", 1242, 2688)
    (tmp_path / "en-US" / ".DS_Store").write_bytes(b"x")
    r = screenshots.scan(tmp_path)
    assert r["ok"], r["problems"]
    assert r["locales"] == ["de-DE", "en-US"]
    assert [f["name"] for f in r["sets"]["en-US"]["APP_IPHONE_67"]] == ["1-first.png", "2-second.png", "10-last.png"]
    assert [f["name"] for f in r["sets"]["en-US"]["APP_IPAD_PRO_3GEN_129"]] == ["ipad.png"]
    assert any("APP_IPAD_PRO_129" in n for n in r["notes"])  # the 2048×2732 choice is explained
    assert list(r["sets"]["de-DE"]) == ["APP_IPHONE_65"]
    only = screenshots.scan(tmp_path, "de-DE")
    assert only["locales"] == ["de-DE"]
    assert "APP_IPHONE_65" in screenshots.scan_display(only)


def test_scan_flat_folder_with_locale(tmp_path: Path) -> None:
    png(tmp_path / "a.png", 1320, 2868)
    jpeg(tmp_path / "b.jpeg", 2064, 2752)
    (tmp_path / "notes.txt").write_text("ignored")
    r = screenshots.scan(tmp_path, "fr-FR")
    assert r["ok"]
    assert set(r["sets"]["fr-FR"]) == {"APP_IPHONE_67", "APP_IPAD_PRO_3GEN_129"}
    assert not screenshots.scan(tmp_path)["sets"].get("fr-FR")


def test_scan_flags_size_alpha_mismatch_and_count(tmp_path: Path) -> None:
    png(tmp_path / "en-US" / "wrong.png", 1000, 2000)
    png(tmp_path / "en-US" / "alpha.png", 1320, 2868, color=6)
    png(tmp_path / "en-US" / "duo.png", 1398, 2034)
    png(tmp_path / "en-US" / "APP_IPHONE_65" / "x.png", 1320, 2868)
    for i in range(11):
        png(tmp_path / "en-US" / "APP_IPAD_PRO_3GEN_129" / f"{i:02}.png", 2064, 2752)
    png(tmp_path / "en-US" / "tablet" / "y.png", 2064, 2752)
    r = screenshots.scan(tmp_path, "en-US")
    text = "\n".join(r["problems"])
    assert not r["ok"]
    assert "wrong.png: 1000×2000 is not an accepted" in text
    assert "alpha.png: PNG has an alpha channel" in text
    assert "duo.png: 1398×2034 is an iPhone Duo size" in text
    assert "x.png: 1320×2868 does not fit APP_IPHONE_65" in text
    assert "APP_IPAD_PRO_3GEN_129: 11 screenshots; Apple allows at most 10" in text
    assert "Unknown folder en-US/tablet" in text


def test_scan_missing_locale_folder(tmp_path: Path) -> None:
    png(tmp_path / "en-US" / "a.png", 1320, 2868)
    r = screenshots.scan(tmp_path, "ja")
    assert "No ja folder" in r["problems"][0]
    with pytest.raises(RankwellError):
        screenshots.scan(tmp_path / "nope")
