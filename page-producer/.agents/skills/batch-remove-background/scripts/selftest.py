"""Exercise cutout integrity, export sizing and delivery checks using fixtures."""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import remove_background as bg


def check_raises(fn, error):
    try:
        fn()
    except error:
        return
    raise AssertionError(f"Expected {error.__name__}")


def main():
    # Explicit parent keeps temporary files inside the caller's allowed workspace.
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cutout-test-", dir=root) as temp:
        base = Path(temp)
        source = Image.new("RGB", (180, 240), (20, 20, 20))
        draw = ImageDraw.Draw(source)
        draw.rectangle((30, 40, 150, 190), fill=(247, 241, 226))
        draw.rectangle((65, 75, 115, 145), fill=(20, 20, 20))  # Letter counter must stay open.
        draw.line((20, 205, 160, 205), fill=(180, 80, 40), width=1)  # Fine colored stroke.
        draw.rectangle((40, 160, 55, 180), fill=(25, 25, 25))  # Dark detail to protect.
        source.save(base / "dark.png")
        protect = Image.new("L", source.size)
        ImageDraw.Draw(protect).rectangle((40, 160, 55, 180), fill=255)
        protect.save(base / "protect.png")
        item = {"id": "dark", "source": "dark.png", "method": "dark", "threshold": 60, "protect_mask": "protect.png"}
        _, original, native, bbox, _, _ = bg.extract(item, base)
        assert native.getpixel((80, 100))[3] == 0, "Letter counter filled"
        assert native.getpixel((45, 170)) == (25, 25, 25, 255), "Protected detail lost"
        assert native.getpixel((80, 205)) == (180, 80, 40, 255), "Fine stroke lost"
        assert np.array_equal(np.asarray(native)[:, :, :3], np.asarray(original)[:, :, :3])
        canvas, _ = bg.make_export(native, bbox)
        assert canvas.size == (2400, 3200) and canvas.mode == "RGBA"
        export_box = canvas.getbbox()
        assert export_box[0] >= 120 and export_box[1] >= 160
        assert export_box[2] <= 2280 and export_box[3] <= 3040
        assert canvas.getpixel((0, 0))[3] == 0

        rgba = Image.new("RGBA", (60, 80))
        ImageDraw.Draw(rgba).rectangle((10, 10, 50, 70), fill=(90, 130, 210, 128))
        rgba.save(base / "alpha.png")
        _, _, alpha, _, _, _ = bg.extract({"source": "alpha.png", "method": "alpha"}, base)
        assert alpha.getpixel((20, 20)) == (90, 130, 210, 128)
        soft = Image.new("L", rgba.size)
        ImageDraw.Draw(soft).rectangle((10, 10, 50, 70), fill=128)
        soft.save(base / "soft.png")
        _, _, masked, _, _, _ = bg.extract({"source": "alpha.png", "method": "mask", "mask": "soft.png"}, base)
        assert masked.getpixel((20, 20)) == (90, 130, 210, 64)
        check_raises(lambda: bg.read_mask(base / "alpha.png", rgba.size, (0, 0, 60, 80)), ValueError)

        light = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(light).rectangle((20, 20, 80, 80), fill=(15, 30, 40))
        light.save(base / "light.png")
        for method, args in [("light", {"threshold": 220}), ("color", {"background": [255, 255, 255], "tolerance": 20})]:
            _, _, im, _, _, _ = bg.extract({"source": "light.png", "method": method, **args}, base)
            assert im.getpixel((0, 0))[3] == 0 and im.getpixel((50, 50)) == (15, 30, 40, 255)
        check_raises(lambda: bg.extract({"source": "light.png", "method": "alpha"}, base), ValueError)
        check_raises(lambda: bg.crop_box([0.8, 0, 0.2, 1], (100, 100)), ValueError)

        edge_source = Image.new("RGB", (30, 30), (20, 20, 20))
        ImageDraw.Draw(edge_source).rectangle((5, 5, 25, 25), fill=(130, 130, 130))
        ImageDraw.Draw(edge_source).rectangle((10, 10, 20, 20), fill=(240, 240, 240))
        edge_source.save(base / "edge.png")
        _, _, edged, _, _, _ = bg.extract({"source": "edge.png", "method": "dark", "threshold": 20, "softness": 220, "edge_background": [20, 20, 20]}, base)
        assert edged.getpixel((8, 8))[3] == 128
        assert 238 <= edged.getpixel((8, 8))[0] <= 240  # Recover edge color from known flat matte.
        assert edged.getpixel((15, 15)) == (240, 240, 240, 255)  # Core never changed.

        config = base / "batch.json"
        config.write_text(json.dumps({"items": [item, {"id": "alpha", "source": "alpha.png", "method": "alpha"}]}), encoding="utf-8")
        output = base / "output"
        assert bg.run(config, output, True) == 0
        check_raises(lambda: bg.run(config, output, False), FileExistsError)
        check_raises(lambda: bg.package(output), ValueError)  # Pending QA cannot ship.
        report_path = output / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert all(row["native_rgb_unchanged"] for row in report["items"])
        assert any("Upscaled" in warning for warning in report["items"][0]["warnings"])
        for row in report["items"]:
            row["visual_review"] = "passed"  # Synthetic expectations above are checked.
        report_path.write_text(json.dumps(report), encoding="utf-8")
        delivery = bg.package(output)
        with zipfile.ZipFile(delivery) as z:
            assert z.testzip() is None
            assert len([n for n in z.namelist() if n.startswith("png/")]) == 2
        with (output / "png" / "dark.png").open("ab") as f:
            f.write(b"tamper")
        check_raises(lambda: bg.package(output), ValueError)

        bad_config = base / "partial.json"
        bad_config.write_text(json.dumps({"items": [item, {"id": "missing", "source": "missing.png", "method": "alpha"}]}), encoding="utf-8")
        partial = base / "partial"
        assert bg.run(bad_config, partial, False) == 1
        assert json.loads((partial / "report.json").read_text())["items"][1]["status"] == "failed"
        check_raises(lambda: bg.package(partial), ValueError)
    print("PASS: RGB, fine strokes, holes, protected detail, soft alpha, methods, 2400x3200, margins, failures, no overwrite, QA gate, ZIP/hash integrity")


if __name__ == "__main__":
    main()
