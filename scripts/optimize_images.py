#!/usr/bin/env python3
"""Compress and downscale images in img/. Keeps originals in img/originals/.

Usage:
  python3 scripts/optimize_images.py            # all images in img/
  python3 scripts/optimize_images.py --dry-run  # preview only
  python3 scripts/optimize_images.py --max-width 1600
"""

import os
import sys
import argparse
from pathlib import Path

from PIL import Image

MAX_JPEG_WIDTH = 2048   # web size (thumbnails + lightbox)
JPEG_QUALITY = 82
PNG_QUANT_COLORS = 256
MAX_PNG_WIDTH = 2048
# Only quantize PNGs >= this size (protect QR codes / small logos).
PNG_QUANT_MIN_BYTES = 60 * 1024
# Unserved archive of originals (see "exclude" in _config.yml).
ORIGINALS_DIR = Path("img") / "originals"

Image.MAX_IMAGE_PIXELS = None  # allow huge image headers


def human(n: int) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def archive_original(path: Path, dry: bool) -> bool:
    """Archive the un-optimized file to img/originals/ once.

    Runs before optimization, so it copies the true original. Never
    overwrites an existing archive. Returns True if a new archive was made."""
    if not path.is_file():
        return False
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ORIGINALS_DIR / path.name

    if dest.exists():  # keep the first (original) copy, never overwrite
        return False
    if dry:
        return True
    import shutil
    shutil.copy2(path, dest)
    return True


def is_new_image(path: Path) -> bool:
    """True if no archived original exists yet, i.e. this image was never processed."""
    return not (ORIGINALS_DIR / path.name).exists()


def process_file(path: Path, dry: bool, max_width: int = MAX_JPEG_WIDTH) -> tuple[str, int, int]:
    """Compress one image; returns (type, before, after) bytes."""
    suffix = path.suffix.lower()
    before = path.stat().st_size

    if suffix in (".jpg", ".jpeg"):
        return _process_jpeg(path, dry, before, max_width)
    if suffix == ".png":
        return _process_png(path, dry, before, max_width)
    return (suffix.lstrip(".").upper(), 0, 0)


def _process_jpeg(path: Path, dry: bool, before: int, max_width: int) -> tuple[str, int, int]:
    img = Image.open(path)
    img.load()
    img = img.convert("RGB")

    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, max(1, round(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)

    if dry:
        return ("JPEG", before, 0)

    # Re-encoding drops metadata (EXIF).
    img.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return ("JPEG", before, path.stat().st_size)


def _process_png(path: Path, dry: bool, before: int, max_width: int) -> tuple[str, int, int]:
    img = Image.open(path)
    img.load()

    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, max(1, round(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)

    # Quantize larger RGB/RGBA images only (skip QR codes / small logos).
    if before >= PNG_QUANT_MIN_BYTES and img.mode in ("RGB", "RGBA"):
        try:
            from io import BytesIO

            has_alpha = img.mode == "RGBA"
            quantized = img.quantize(
                colors=PNG_QUANT_COLORS,
                method=Image.MEDIANCUT,
                dither=Image.FLOYDSTEINBERG,
            )

            def size_of(im):
                b = BytesIO()
                im.save(b, "PNG", optimize=True)
                return len(b.getvalue())

            if not has_alpha:
                if size_of(quantized) < size_of(img) * 0.95:
                    img = quantized
            elif size_of(quantized) < size_of(img):
                img = quantized
        except Exception:
            pass  # keep original encoding

    if dry:
        return ("PNG", before, 0)

    img.save(path, "PNG", optimize=True)
    return ("PNG", before, path.stat().st_size)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="image/dir paths (default: img/)")
    parser.add_argument("--dry-run", action="store_true", help="preview only")
    parser.add_argument(
        "--max-width", type=int, default=MAX_JPEG_WIDTH,
        help=f"max JPEG width (default: {MAX_JPEG_WIDTH})",
    )
    parser.add_argument(
        "--only-new", action="store_true",
        help="only process images without an archived original (used in CI)",
    )
    args = parser.parse_args()

    max_width = args.max_width

    roots = args.paths or ["img"]
    files: list[Path] = []
    for r in roots:
        p = Path(r)
        if p.is_dir():
            files.extend(p.rglob("*"))
        elif p.is_file():
            files.append(p)
        else:
            print(f"skip (not found): {r}")

    files = [
        f for f in files
        if f.suffix.lower() in (".jpg", ".jpeg", ".png")
        and ORIGINALS_DIR not in f.parents  # never process the archive
    ]
    if args.only_new:
        files = [f for f in files if is_new_image(f)]

    if not files:
        print("No JPEG/PNG images found.")
        return 0

    mode = "DRY-RUN" if args.dry_run else "Optimize"
    print(f"{'='*60}\n{mode} — {len(files)} images\n{'='*60}")

    total_before = 0
    total_after = 0
    changed = 0
    archived = 0
    for f in sorted(files):
        try:
            if archive_original(f, args.dry_run):
                archived += 1
            _typ, before, after = process_file(f, args.dry_run, max_width)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {f.relative_to('.')}: error — {e}")
            continue

        total_before += before
        total_after += after
        if before and before > after and not args.dry_run:
            saved = before - after
            pct = 100 * (1 - after / before)
            changed += 1
            print(f"  ✓ {f.relative_to('.')}: {human(before)} → {human(after)} "
                  f"(-{pct:.0f}%, −{human(saved)})")
        elif args.dry_run and before:
            print(f"  · {f.relative_to('.')}: {human(before)}")
        else:
            print(f"  · {f.relative_to('.')}: unchanged ({human(before)})")

    print(f"{'='*60}")
    if total_after:
        print(f"Total: {human(total_before)} → {human(total_after)} "
              f"({100*(1-total_after/total_before):.0f}% saved)")
        print(f"Changed files: {changed}")
    else:
        print(f"Total: {human(total_before)}")
    if archived:
        print(f"Archived originals to {ORIGINALS_DIR}/: {archived}")
    print("Hint: use --dry-run to preview.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
