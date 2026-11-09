"""Pixel-preserving batch cutouts with reviewed 2400x3200 PNG delivery."""
import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

SIZE = (2400, 3200)
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def number(value, lo, hi, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{label} must be a finite number in [{lo}, {hi}]")
    return value


def load_image(path):
    with Path(path).open("rb") as f:
        header = f.read(25)
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) > 24 and header[24] > 8:
        raise ValueError("Use an explicitly converted 8-bit source")
    with Image.open(path) as im:
        if getattr(im, "n_frames", 1) != 1:
            raise ValueError("Animated/multipage sources are unsupported")
        bits = im.tag_v2.get(258, (8,)) if hasattr(im, "tag_v2") else (8,)
        bits = (bits,) if isinstance(bits, int) else bits
        if im.mode.startswith("I") or im.mode == "F" or any(int(b) > 8 for b in bits):
            raise ValueError("Use an explicitly converted 8-bit source")
        mode = im.mode
        icc = im.info.get("icc_profile")
        return ImageOps.exif_transpose(im).convert("RGBA"), mode, icc


def crop_box(values, size):
    if not isinstance(values, list) or len(values) != 4:
        raise ValueError("crop needs four normalized coordinates")
    v = [number(x, 0, 1, "crop") for x in values]
    if v[0] >= v[2] or v[1] >= v[3]:
        raise ValueError("crop must have positive width and height")
    box = tuple(round(x * d) for x, d in zip(v, (*size, *size)))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("crop rounds to an empty region")
    return box


def read_mask(path, full_size, box):
    with Image.open(path) as im:
        if getattr(im, "n_frames", 1) != 1 or "A" in im.getbands() or "transparency" in im.info:
            raise ValueError("Mask must be a static grayscale/RGB image without alpha")
        im = ImageOps.exif_transpose(im)
        rgb = np.asarray(im.convert("RGB"))
        if not np.array_equal(rgb[:, :, 0], rgb[:, :, 1]) or not np.array_equal(rgb[:, :, 1], rgb[:, :, 2]):
            raise ValueError("Mask RGB channels must be equal (grayscale)")
        im = im.convert("L")
        if im.size == full_size:
            im = im.crop(box)
        if im.size != (box[2] - box[0], box[3] - box[1]):
            raise ValueError("Mask dimensions must match the full source or crop")
        return np.asarray(im, dtype=np.uint8)


def threshold_alpha(strength, softness):
    softness = number(softness, 0, 255, "softness")
    if softness == 0:
        return (strength > 0).astype(np.uint8) * 255
    return np.rint(np.clip(strength / softness, 0, 1) * 255).astype(np.uint8)


def extract(item, base):
    source = (base / item["source"]).resolve()
    image, original_mode, icc = load_image(source)
    box = crop_box(item.get("crop", [0, 0, 1, 1]), image.size)
    native = image.crop(box)
    pixels = np.array(native)
    rgb = pixels[:, :, :3].astype(np.float32)
    method = item["method"]
    if method == "alpha":
        if pixels[:, :, 3].min() == 255:
            raise ValueError("alpha mode needs existing transparency in the crop")
        alpha = np.full(pixels.shape[:2], 255, dtype=np.uint8)
    elif method == "dark":
        alpha = threshold_alpha(rgb.max(axis=2) - number(item["threshold"], 0, 255, "threshold"), item.get("softness", 0))
    elif method == "light":
        alpha = threshold_alpha(number(item["threshold"], 0, 255, "threshold") - rgb.min(axis=2), item.get("softness", 0))
    elif method == "color":
        bg = item["background"]
        if not isinstance(bg, list) or len(bg) != 3:
            raise ValueError("background must be an RGB triplet")
        bg = np.array([number(x, 0, 255, "background") for x in bg])
        tolerance = number(item["tolerance"], 0, 442, "tolerance")
        alpha = threshold_alpha(np.linalg.norm(rgb - bg, axis=2) - tolerance, item.get("softness", 0))
    elif method == "mask":
        alpha = read_mask(base / item["mask"], image.size, box)
    else:
        raise ValueError(f"Unknown method: {method}")
    if item.get("protect_mask"):
        alpha = np.maximum(alpha, read_mask(base / item["protect_mask"], image.size, box))
    if "polygon" in item:
        poly = item["polygon"]
        if not isinstance(poly, list) or len(poly) < 3:
            raise ValueError("polygon needs at least three normalized points")
        points = []
        for point in poly:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Each polygon point needs x and y")
            x, y = [number(v, 0, 1, "polygon") for v in point]
            points.append((round(x * image.width) - box[0], round(y * image.height) - box[1]))
        region = Image.new("L", native.size)
        ImageDraw.Draw(region).polygon(points, fill=255)
        alpha = np.minimum(alpha, np.asarray(region))
    source_alpha = pixels[:, :, 3].copy()
    pixels[:, :, 3] = ((source_alpha.astype(np.uint16) * alpha.astype(np.uint16) + 127) // 255).astype(np.uint8)
    if "edge_background" in item:
        edge_bg = item["edge_background"]
        if not isinstance(edge_bg, list) or len(edge_bg) != 3:
            raise ValueError("edge_background must be an RGB triplet")
        edge_bg = np.array([number(x, 0, 255, "edge_background") for x in edge_bg])
        edge = (pixels[:, :, 3] > 0) & (pixels[:, :, 3] < 255) & (source_alpha == 255)
        a = pixels[:, :, 3][edge].astype(np.float32)[:, None] / 255
        pixels[:, :, :3][edge] = np.rint(np.clip((rgb[edge] - edge_bg * (1 - a)) / a, 0, 255)).astype(np.uint8)
    cutout = Image.fromarray(pixels)
    bbox = cutout.getbbox()
    if not bbox:
        raise ValueError("Empty cutout; correct the region, threshold or mask")
    if pixels[:, :, 3].min() != 0:
        raise ValueError("No transparent pixel; this does not demonstrate background removal")
    opaque = pixels[:, :, 3] == 255
    assert np.array_equal(np.asarray(native)[:, :, :3][opaque], pixels[:, :, :3][opaque])
    warnings = []
    if original_mode not in ("RGB", "RGBA"):
        warnings.append(f"Source decoded from {original_mode} to 8-bit RGBA; inspect colors")
    if original_mode == "CMYK":
        icc = None  # A CMYK profile must not be attached to an RGB result.
    if method != "alpha":
        warnings.append("Mask edges, missing details and residual background need visual inspection")
    if min(bbox[:2]) == 0 or bbox[2] == cutout.width or bbox[3] == cutout.height:
        warnings.append("Artwork touches a crop edge; check for clipping")
    return source, native, cutout, bbox, icc, warnings


def make_export(cutout, bbox):
    artwork = cutout.crop(bbox)
    scale = min(2160 / artwork.width, 2880 / artwork.height)
    size = (max(1, round(artwork.width * scale)), max(1, round(artwork.height * scale)))
    artwork = artwork.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", SIZE)
    canvas.paste(artwork, ((SIZE[0] - size[0]) // 2, (SIZE[1] - size[1]) // 2))
    return canvas, scale


def thumbnail_panel(im, color, width=240, height=280):
    panel = Image.new("RGB", (width, height), color)
    im = im.copy()
    im.thumbnail((width - 16, height - 16), Image.Resampling.LANCZOS)
    xy = ((width - im.width) // 2, (height - im.height) // 2)
    panel.paste(im, xy, im if im.mode == "RGBA" else None)
    return panel


def zip_files(output, name):
    target = output / name
    if target.exists():
        raise FileExistsError(f"Package already exists: {target}")
    files = [output / "report.json", output / "review.jpg", *sorted((output / "png").glob("*.png")), *sorted((output / "native").glob("*.png"))]
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            z.write(path, path.relative_to(output).as_posix())
    return target


def package(output):
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    rows = report["items"]
    if not rows or any(r.get("status") != "exported" or r.get("visual_review") != "passed" for r in rows):
        raise ValueError("Every item needs a successful export and visual_review: passed")
    expected = set()
    for row in rows:
        if not SAFE_ID.fullmatch(row["id"]) or row["id"].upper() in WINDOWS_RESERVED:
            raise ValueError("Invalid report item ID")
        expected.add(f'{row["id"]}.png')
        for kind in ("png", "native"):
            path = output / kind / f'{row["id"]}.png'
            if digest(path) != row[f"{kind}_sha256"]:
                raise ValueError(f"File changed after export: {path}")
            if kind == "png":
                with Image.open(path) as im:
                    if im.size != SIZE or im.mode != "RGBA" or im.getchannel("A").getextrema()[0] != 0:
                        raise ValueError(f"Invalid output dimensions/mode/alpha: {path}")
    for kind in ("png", "native"):
        if {p.name for p in (output / kind).glob("*.png")} != expected:
            raise ValueError(f"Unexpected/missing PNGs in {kind}")
    return zip_files(output, "delivery.zip")


def run(config, output, preview_zip):
    data = json.loads(config.read_text(encoding="utf-8-sig"))
    items = data["items"]
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a nonempty list")
    ids = [r["id"] for r in items]
    if any(not isinstance(i, str) or not SAFE_ID.fullmatch(i) or i.upper() in WINDOWS_RESERVED for i in ids) or len(set(i.lower() for i in ids)) != len(ids):
        raise ValueError("Use unique safe IDs; names are case-insensitive")
    base = config.parent
    for item in items:
        for key in ("source", "mask", "protect_mask"):
            if key in item and (base / item[key]).resolve().is_relative_to(output):
                raise ValueError("Inputs cannot be inside the output directory")
    output.mkdir(parents=True, exist_ok=False)
    (output / "png").mkdir()
    (output / "native").mkdir()
    rows = []
    sheet = Image.new("RGB", (960, 310 * len(items)), "#e1e1e1")
    draw = ImageDraw.Draw(sheet)
    for index, item in enumerate(items):
        row = {"id": item["id"], "source": str((base / item["source"]).resolve()), "settings": item, "quote": item.get("quote"), "visual_review": "pending"}
        try:
            source, original, cutout, bbox, icc, warnings = extract(item, base)
            exported, scale = make_export(cutout, bbox)
            options = {"icc_profile": icc} if icc else {}
            native_path = output / "native" / f'{item["id"]}.png'
            png_path = output / "png" / f'{item["id"]}.png'
            cutout.save(native_path, **options)
            exported.save(png_path, **options)
            if scale > 1:
                warnings.append("Upscaled using Lanczos; no original detail recovered. Inspect at 100% before approval")
            unchanged = np.array_equal(np.asarray(original)[:, :, :3], np.asarray(cutout)[:, :, :3])
            if not unchanged:
                warnings.append("Partial-alpha edge RGB adjusted using the configured background; opaque core RGB preserved")
            row.update(status="exported", source_sha256=digest(source), native_sha256=digest(native_path), png_sha256=digest(png_path), native_size=list(cutout.size), artwork_source_size=[bbox[2] - bbox[0], bbox[3] - bbox[1]], output_size=list(SIZE), scale=scale, native_rgb_unchanged=unchanged, native_opaque_rgb_unchanged=True, warnings=warnings)
            panels = [thumbnail_panel(original, "#999999"), thumbnail_panel(exported, "white"), thumbnail_panel(exported, "black"), thumbnail_panel(exported, "#777777")]
            for col, panel in enumerate(panels):
                sheet.paste(panel, (col * 240, index * 310 + 25))
            draw.text((8, index * 310 + 7), f'{item["id"]} | source | white | black | gray', fill="black")
        except Exception as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            draw.text((8, index * 310 + 8), f'{item["id"]}: FAILED - see report.json', fill="red")
        rows.append(row)
    report = {"schema_version": 1, "canvas": list(SIZE), "method": "source pixels and alpha masks; no AI redraw", "items": rows}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sheet.save(output / "review.jpg", quality=92)
    if preview_zip:
        zip_files(output, "preview-package.zip")
    success = sum(r["status"] == "exported" for r in rows)
    print(json.dumps({"exported": success, "failed": len(rows) - success, "visual_review": "pending", "output": str(output)}, ensure_ascii=False))
    return 0 if success == len(rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--zip", action="store_true", dest="preview_zip")
    parser.add_argument("--package", type=Path, help="Verify reviewed existing results and create delivery.zip")
    args = parser.parse_args()
    try:
        if args.package:
            if args.config or args.output or args.preview_zip:
                parser.error("--package cannot be combined with export options")
            print(package(args.package.resolve()))
            return 0
        if not args.config or not args.output:
            parser.error("--config and --output are required for export")
        return run(args.config.resolve(), args.output.resolve(), args.preview_zip)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
