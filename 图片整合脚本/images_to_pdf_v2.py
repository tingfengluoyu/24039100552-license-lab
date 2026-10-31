#!/usr/bin/env python3
"""Merge images in a folder into a single PDF, with automatic ordering.

This is a rewrite of ``images_to_pdf.py``.  The original required images to be
renamed to ``1.jpg``, ``2.jpg``, ... because the only ordering key available
was the numeric file name.  This version keeps that mode (``--sort number``)
but adds real ordering strategies so screenshots can be dropped in a folder
untouched.

Ordering modes (``--sort``)
--------------------------
auto     (default) Pick a key automatically.  Uses EXIF capture time when the
         images carry it; otherwise compares filesystem creation time and
         modification time and detects the "batch copy" signature (many files
         created within a couple of seconds of each other while the
         modification times stay spread out), which means the creation time is
         useless and the modification time is the real capture order.
created  Filesystem creation time (Windows ``st_ctime``, macOS/BSD
         ``st_birthtime``).
modified Filesystem modification time.  Survives copying, zipping, download
         and sync, so it is usually the safest pure-filesystem key.
exif     EXIF ``DateTimeOriginal`` / ``DateTimeDigitized`` / ``DateTime``.
name     Natural ("human") order of the file name, so ``shot2.png`` sorts
         before ``shot10.png``.
number   Numeric stem order - the behaviour of the original script.

Ties in any time-based mode fall back to the natural file-name order, so the
result is always deterministic.  ``--print-order`` shows the planned order and
the timestamp source per page without writing a PDF.

Output
------
The PDF is built as ``<name>.pdf.part`` next to the target and moved into place
only once it is complete, so a failure (bad image, full disk, closed window,
Ctrl-C) never damages a PDF that was there before.  Image streams are written as
compact binary by default; ``--pdf-ascii85 on`` restores reportlab's ASCII85
wrapping, which makes the file about 20% larger.

Examples
--------
    python images_to_pdf_v2.py                       # current folder, auto
    python images_to_pdf_v2.py D:\\shots -n 复习.pdf
    python images_to_pdf_v2.py D:\\shots --sort modified --print-order
    python images_to_pdf_v2.py D:\\shots --sort number   # legacy behaviour
    python images_to_pdf_v2.py D:\\shots -r --min-side 400 --dedup
    python images_to_pdf_v2.py D:\\shots --page fit --margin 0 --encode jpeg
    python images_to_pdf_v2.py D:\\shots --save-order order.txt

Requires: pillow, reportlab  (python -m pip install pillow reportlab)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
    from reportlab import rl_config
    from reportlab.lib.pagesizes import A4, LETTER
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
except ImportError as exc:  # pragma: no cover - environment guard
    missing = exc.name or "required package"
    raise SystemExit(
        f"Missing dependency: {missing}\n"
        "Install dependencies with: python -m pip install pillow reportlab"
    ) from exc


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_OUTPUT_NAME = "merged_images.pdf"
PAGE_SIZES = {"a4": A4, "letter": LETTER, "fit": None}
MM = 72.0 / 25.4  # millimetres -> PDF points
EXIF_ORIENTATION = 274
EXIF_TIME_TAGS = (36867, 36868, 306)  # DateTimeOriginal, DateTimeDigitized, DateTime
TIME_SOURCES = ("exif", "created", "modified")
ALL_SOURCES = ("auto",) + TIME_SOURCES + ("name", "number")


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
@dataclass
class ImageEntry:
    """One candidate image plus everything the sorters may need."""

    path: Path
    rel: str
    width: int
    height: int
    modified: float
    created: float | None = None
    exif_time: float | None = None
    orientation: int | None = None
    digest: str | None = None
    has_alpha: bool = False

    @property
    def name(self) -> str:
        return self.path.name

    def time_for(self, source: str) -> float | None:
        if source == "exif":
            return self.exif_time
        if source == "created":
            return self.created
        if source == "modified":
            return self.modified
        return None


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def natural_key(text: str) -> list:
    """Sort key that orders embedded numbers by value: shot2 < shot10."""
    parts: list = []
    for chunk in re.split(r"(\d+)", text.lower()):
        if not chunk:
            continue
        parts.append((0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk))
    return parts


def parse_exif_time(raw: object) -> float | None:
    """EXIF datetimes are 'YYYY:MM:DD HH:MM:SS' (occasionally with junk)."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().rstrip("\x00")
    if not text or text.startswith("0000"):
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def file_creation_time(stat: os.stat_result) -> float | None:
    """Best-effort 'when did this file appear' timestamp.

    Windows stores the real creation time in st_ctime; on POSIX st_ctime is the
    metadata-change time, which is why we only trust it on Windows, and use
    st_birthtime where the platform provides it.
    """
    birthtime = getattr(stat, "st_birthtime", None)
    if birthtime:
        return float(birthtime)
    if os.name == "nt":
        return float(stat.st_ctime)
    return None


def fmt_time(value: float | None) -> str:
    return "-" if value is None else datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def is_clustered(values: Sequence[float], window: float = 3.0, share: float = 0.8) -> bool:
    """True when most values sit inside a tiny window (batch copy/move signature)."""
    if len(values) < 4:
        return False
    ordered = sorted(values)
    if ordered[-1] - ordered[0] <= window:
        return True
    width = max(2, int(len(ordered) * share))
    return min(
        ordered[i + width - 1] - ordered[i] for i in range(len(ordered) - width + 1)
    ) <= window


def spread(values: Sequence[float]) -> float:
    return 0.0 if not values else max(values) - min(values)


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def iter_image_paths(target_dir: Path, recursive: bool) -> Iterable[Path]:
    """Yield every candidate file below ``target_dir``, entering each folder once.

    Windows directory junctions are not reported as links by ``Path.is_dir()``,
    so a junction pointing back at an ancestor was descended into again and again
    (a self-referential one produced 43 pages instead of 2, and a folder reachable
    by two routes produced duplicate pages).  Folders are therefore entered at most
    once per *real* path.  Files of every extension are yielded here; the extension
    filter lives in collect_images so that a skipped file can be reported.
    """
    seen: set[str] = set()

    def walk(folder: Path) -> Iterator[Path]:
        try:
            real = os.path.realpath(folder)
        except OSError:
            return
        if real in seen:
            return
        seen.add(real)
        try:
            children = sorted(folder.iterdir(), key=lambda item: natural_key(str(item)))
        except OSError:
            return
        for child in children:
            try:
                if child.is_dir():
                    yield from walk(child)
                elif child.is_file():
                    yield child
            except OSError:
                continue

    if recursive:
        yield from walk(target_dir)
        return
    try:
        children = sorted(target_dir.iterdir(), key=lambda item: natural_key(str(item)))
    except OSError:
        return
    for child in children:
        try:
            if child.is_file():
                yield child
        except OSError:
            continue


IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"GIF87a",  # GIF
    b"GIF89a",
    b"BM",  # BMP
    b"II*\x00",  # TIFF (little endian)
    b"MM\x00*",  # TIFF (big endian)
    b"RIFF",  # WebP and friends
    b"\x00\x00\x01\x00",  # ICO
)

# Reasons that mean "the user asked for this to be left out" rather than
# "something went wrong".  --strict ignores these and aborts on everything else.
INTENTIONAL_SKIP_MARKERS = (
    "smaller than --min-side",
    "duplicate of",
    "not numbered (--only-numbered)",
    "unsupported file type",
)


def looks_like_image(path: Path) -> bool:
    """Magic-number sniff, used only to explain why an unsupported file was left out."""
    try:
        with path.open("rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    if any(head.startswith(magic) for magic in IMAGE_MAGIC):
        return True
    return head[4:8] == b"ftyp"  # HEIC/AVIF/MP4 family


def is_fatal_skip(reason: str) -> bool:
    """True when a skipped file means the PDF is missing something it should hold."""
    return not reason.startswith(INTENTIONAL_SKIP_MARKERS)


def file_digest(path: Path, chunk: int = 1 << 20) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def scan_image(path: Path, rel: str) -> ImageEntry:
    """Read metadata without decoding the whole image."""
    stat = path.stat()
    entry = ImageEntry(
        path=path,
        rel=rel,
        width=0,
        height=0,
        modified=stat.st_mtime,
        created=file_creation_time(stat),
    )
    with Image.open(path) as image:
        entry.width, entry.height = image.size
        entry.has_alpha = image_has_alpha(image)
        entry.orientation = image.getexif().get(EXIF_ORIENTATION)
        if entry.orientation in (5, 6, 7, 8):
            entry.width, entry.height = entry.height, entry.width
        exif = image.getexif()
        for tag in EXIF_TIME_TAGS:
            stamp = parse_exif_time(exif.get(tag))
            if stamp is not None:
                entry.exif_time = stamp
                break
    return entry


def collect_images(target_dir: Path, args: argparse.Namespace) -> tuple[list[ImageEntry], list[tuple[Path, str]]]:
    """Return (entries, skipped) where skipped is [(path, reason)]."""
    entries: list[ImageEntry] = []
    skipped: list[tuple[Path, str]] = []
    seen: dict[str, Path] = {}
    min_side = args.min_side

    for path in sorted(iter_image_paths(target_dir, args.recursive), key=lambda p: natural_key(str(p))):
        rel = path.relative_to(target_dir).as_posix()
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            if looks_like_image(path):
                suffix = path.suffix or "no extension"
                skipped.append((path, f"unsupported file type '{suffix}' (looks like an image)"))
            continue
        try:
            entry = scan_image(path, rel)
        except UnidentifiedImageError:
            skipped.append((path, "not a readable image"))
            continue
        except OSError as exc:
            if isinstance(exc, PermissionError):
                skipped.append((path, "no read permission (run as administrator?)"))
            else:
                skipped.append((path, f"unreadable ({exc.__class__.__name__})"))
            continue
        except Image.DecompressionBombError:
            skipped.append((path, "too large for Pillow's safety limit"))
            continue

        if min_side and min(entry.width, entry.height) < min_side:
            skipped.append((path, f"smaller than --min-side {min_side}px"))
            continue

        if args.only_numbered and not re.fullmatch(r"\d+", path.stem.strip()):
            skipped.append((path, "not numbered (--only-numbered)"))
            continue

        if args.dedup:
            digest = file_digest(path)
            entry.digest = digest
            if digest in seen:
                skipped.append((path, f"duplicate of {seen[digest].name}"))
                continue
            seen[digest] = path

        entries.append(entry)

    if args.only_numbered:
        by_number: dict[int, list[ImageEntry]] = {}
        for entry in entries:
            by_number.setdefault(int(entry.path.stem.strip()), []).append(entry)
        duplicates = {num: items for num, items in by_number.items() if len(items) > 1}
        if duplicates:
            details = "\n".join(
                f"  {num}: {', '.join(item.rel for item in sorted(items, key=lambda e: e.rel))}"
                for num, items in sorted(duplicates.items())
            )
            raise SystemExit(f"Duplicate image numbers found (--only-numbered):\n{details}")

    return entries, skipped


# --------------------------------------------------------------------------- #
# ordering
# --------------------------------------------------------------------------- #
def resolved_source(entry: ImageEntry, source: str) -> float | None:
    """Timestamp for ``source``, falling back to the other clock when missing."""
    if source == "exif":
        return entry.exif_time if entry.exif_time is not None else (
            entry.created if entry.created is not None else entry.modified
        )
    if source == "created":
        return entry.created if entry.created is not None else entry.modified
    return entry.modified


def choose_auto_source(entries: list[ImageEntry]) -> tuple[str, str]:
    """Pick the most trustworthy time key and explain the choice."""
    exif_known = [e for e in entries if e.exif_time is not None]
    coverage = len(exif_known) / len(entries)

    if len(entries) < 2:
        return "created", "single file, ordering is irrelevant"
    if coverage >= 0.9:
        return "exif", f"EXIF capture time present on {len(exif_known)}/{len(entries)} image(s)"

    created = [e.created for e in entries]
    known_created = [value for value in created if value is not None]
    modified = [e.modified for e in entries]

    if not known_created:
        return "modified", "no creation time available on this platform"

    if is_clustered(known_created) and not is_clustered(modified):
        return (
            "modified",
            "creation times cluster inside a few seconds but modification times are spread out, "
            "which is the signature of a batch copy - creation time is not the capture time here",
        )

    created_spread = spread(known_created)
    modified_spread = spread(modified)
    if created_spread < 1.0 and modified_spread < 1.0:
        return (
            "created",
            "every candidate timestamp is identical (files were created within the same second); "
            "the file-name order breaks the tie",
        )
    if created_spread >= modified_spread or created_spread > 1.0:
        return (
            "created",
            f"creation times are distinct (span {created_spread / 60:.1f} min, "
            f"modification span {modified_spread / 60:.1f} min)",
        )
    return (
        "modified",
        "creation times are degenerate while modification times differ, "
        "so the modification clock carries the real order",
    )


def order_entries(entries: list[ImageEntry], args: argparse.Namespace) -> tuple[list[ImageEntry], str, str | None]:
    """Return (ordered entries, source used, human-readable note)."""
    note: str | None = None
    source = args.sort

    if args.order_file:
        ordered = apply_order_file(entries, args.order_file)
        if ordered is not None:
            return ordered, "order-file", f"explicit order from {args.order_file}"
        note = f"order file {args.order_file} did not match any image; falling back to --sort {args.sort}"

    if source == "auto":
        source, why = choose_auto_source(entries)
        note = f"auto chose '{source}': {why}"
    elif source in TIME_SOURCES:
        missing = sum(1 for e in entries if e.time_for(source) is None)
        if missing:
            note = f"{missing} image(s) have no {source} timestamp; they fall back to the other clock"
    elif source == "number":
        if any(re.fullmatch(r"\d+", e.path.stem.strip()) is None for e in entries):
            note = "non-numeric names sort after numeric ones in 'number' mode"

    if source == "name":
        ordered = sorted(entries, key=lambda e: natural_key(e.rel))
    elif source == "number":
        ordered = sorted(
            entries,
            key=lambda e: (
                0,
                int(e.path.stem.strip()),
                natural_key(e.rel),
            )
            if re.fullmatch(r"\d+", e.path.stem.strip())
            else (1, 0, natural_key(e.rel)),
        )
    else:
        ordered = sorted(
            entries,
            key=lambda e: (resolved_source(e, source) or 0.0, natural_key(e.rel)),
        )

    if args.reverse:
        ordered = list(reversed(ordered))
    return ordered, source, note


def apply_order_file(entries: list[ImageEntry], order_path: Path | str) -> list[ImageEntry] | None:
    """Reorder using a text file of file names; unknown lines are reported."""
    order_path = Path(order_path)
    if not order_path.exists():
        raise SystemExit(f"Order file does not exist: {order_path}")

    wanted: list[str] = []
    for line in order_path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            wanted.append(text)

    index = {entry.rel: entry for entry in entries}
    index.update({entry.name: entry for entry in entries})

    result: list[ImageEntry] = []
    used: set[str] = set()
    missing: list[str] = []
    for text in wanted:
        entry = index.get(text) or index.get(Path(text).name)
        if entry is None:
            missing.append(text)
            continue
        if entry.rel in used:
            continue
        used.add(entry.rel)
        result.append(entry)

    if missing:
        print("Order file entries not found (ignored): " + ", ".join(missing))
    if not result:
        return None

    leftover = [entry for entry in entries if entry.rel not in used]
    if leftover:
        print(f"Order file covered {len(result)} image(s); appending {len(leftover)} remaining in name order.")
        result.extend(sorted(leftover, key=lambda e: natural_key(e.rel)))
    return result


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
@dataclass
class Prepared:
    """A page image ready to be handed to reportlab."""

    reader: ImageReader | str
    width: int
    height: int
    keepalive: list = field(default_factory=list)
    resized: bool = False
    cleanup: list[Path] = field(default_factory=list)


def image_has_alpha(image: Image.Image) -> bool:
    """True when the image carries transparency that reportlab would drop."""
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


def flatten_alpha(image: Image.Image) -> Image.Image:
    """Composite onto white, the way a viewer shows a transparent PNG on paper.

    reportlab converts every non-JPEG image to RGB and silently discards the
    alpha channel, so without this a fully transparent pixel would keep whatever
    colour happens to sit under it (usually black) instead of showing the page.
    """
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def encode_image(image: Image.Image, fmt: str, quality: int) -> io.BytesIO:
    buffer = io.BytesIO()
    if fmt == "jpeg":
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, "white")
            converted = image.convert("RGBA")
            background.paste(converted, mask=converted.split()[-1])
            image = background
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
    else:
        if image.mode == "CMYK":
            image = image.convert("RGB")
        image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer


def _temp_image_path() -> Path | None:
    """A throwaway .jpg path in the system temp folder, or None if unavailable."""
    try:
        handle, name = tempfile.mkstemp(prefix="images_to_pdf_", suffix=".jpg")
    except OSError:
        return None
    os.close(handle)
    return Path(name)


def prepare_page_image(entry: ImageEntry, page_size: tuple[float, float], args: argparse.Namespace) -> Prepared:
    """Downscale/rotate/convert only when needed; otherwise pass the file through.

    Passing a JPEG file straight to reportlab embeds its original bytes untouched
    (best quality, smallest file).  reportlab decodes every other format, so a
    PNG is re-deflated from raw pixels: still lossless, but far larger than the
    original file -- use --encode jpeg when size matters.  A transform is forced
    by EXIF rotation, transparency, grayscale, a format override, or --max-dpi.
    """
    page_w, page_h = page_size
    margin = args.margin * MM
    cap_w = max(1, int((page_w - 2 * margin) / 72.0 * args.max_dpi)) if args.max_dpi else 0
    cap_h = max(1, int((page_h - 2 * margin) / 72.0 * args.max_dpi)) if args.max_dpi else 0

    needs_rotate = entry.orientation not in (None, 1)
    needs_flatten = entry.has_alpha
    needs_convert = args.grayscale or args.encode != "keep" or needs_flatten
    scale = 1.0
    if cap_w and cap_h:
        scale = min(1.0, cap_w / entry.width, cap_h / entry.height)
    needs_resize = scale < 0.999

    if not (needs_rotate or needs_convert or needs_resize):
        return Prepared(reader=ImageReader(str(entry.path)), width=entry.width, height=entry.height)

    with Image.open(entry.path) as raw:
        raw.load()
        image = ImageOps.exif_transpose(raw) or raw
        if needs_flatten:
            image = flatten_alpha(image)
        if needs_resize:
            target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            image = image.resize(target, Image.LANCZOS)
        if args.grayscale and image.mode != "L":
            image = image.convert("L")

    fmt = args.encode
    if fmt == "keep":
        fmt = "jpeg" if entry.path.suffix.lower() in (".jpg", ".jpeg") else "png"

    buffer = encode_image(image, fmt, args.quality)
    if fmt == "jpeg":
        # reportlab byte-passes a JPEG only when it is handed a *.jpg path*: an
        # ImageReader built from a buffer is decoded to RGB and re-deflated, which
        # costs roughly 44 MB per 6 Mpx page in reportlab (it holds every page
        # image until save) and makes the PDF several times larger than the DCT
        # stream it already has.  Writing the encoded bytes to a temp .jpg keeps
        # reportlab's memory flat and the file small.
        temp = _temp_image_path()
        if temp is not None:
            try:
                temp.write_bytes(buffer.getvalue())
            except OSError:
                discard_temp_pdf(temp)
            else:
                return Prepared(
                    reader=str(temp),
                    width=image.width,
                    height=image.height,
                    resized=needs_resize,
                    cleanup=[temp],
                )
    reader = ImageReader(buffer)
    return Prepared(
        reader=reader,
        width=image.width,
        height=image.height,
        keepalive=[buffer],
        resized=needs_resize,
    )


def page_geometry(prepared: Prepared, page_size: tuple[float, float], margin_pt: float) -> tuple[tuple[float, float], float, float, float, float]:
    """Compute the (x, y, w, h) draw box and the final page size for one page.

    An image smaller than its page is normally blown up to fill it (a 40 px
    screenshot must not sit on an A4 sheet as a postage stamp).  The available
    space is therefore never *floored* to something generous: an absurd margin
    (or a sub-1 pt "fit" page for a 1 px image at a huge --dpi) leaves almost no
    room, and the drawing has to shrink instead of overflowing its MediaBox.
    """
    page_w, page_h = page_size
    avail_w = max(0.1, page_w - 2 * margin_pt)
    avail_h = max(0.1, page_h - 2 * margin_pt)
    scale = min(avail_w / prepared.width, avail_h / prepared.height)
    draw_w = prepared.width * scale
    draw_h = prepared.height * scale
    x = (page_w - draw_w) / 2
    y = (page_h - draw_h) / 2
    return (page_w, page_h), x, y, draw_w, draw_h


def render_page(
    pdf: canvas.Canvas,
    entry: ImageEntry,
    args: argparse.Namespace,
    stats: dict[str, int] | None = None,
) -> tuple[float, float]:
    """Draw one image on its own page.  Returns the page size in points."""
    margin_pt = args.margin * MM
    page_size = PAGE_SIZES[args.page]
    if page_size is None:  # --page fit: page matches the image aspect ratio
        # Derived from the *source* pixel size, so --max-dpi only lowers the
        # embedded resolution and never silently shrinks the physical page.
        width_pt = entry.width / args.dpi * 72.0 + 2 * margin_pt
        height_pt = entry.height / args.dpi * 72.0 + 2 * margin_pt
        page_size = (width_pt, height_pt)
        pdf.setPageSize(page_size)

    prepared = prepare_page_image(entry, page_size, args)
    if stats is not None:
        if prepared.resized:
            stats["downscaled"] = stats.get("downscaled", 0) + 1
        if prepared.cleanup:
            stats.setdefault("temp_images", []).extend(prepared.cleanup)

    (page_w, page_h), x, y, draw_w, draw_h = page_geometry(prepared, page_size, margin_pt)
    pdf.drawImage(
        prepared.reader,
        x,
        y,
        width=draw_w,
        height=draw_h,
        preserveAspectRatio=True,
        anchor="c",
    )
    pdf.showPage()
    return page_w, page_h


# --------------------------------------------------------------------------- #
# plan printing
# --------------------------------------------------------------------------- #
def print_plan(entries: list[ImageEntry], source: str) -> None:
    print(f"{'#':>4} | {'source':<8} | {'timestamp':<19} | file")
    print("-" * 78)
    for index, entry in enumerate(entries, start=1):
        stamp = resolved_source(entry, source) if source in TIME_SOURCES else None
        print(f"{index:>4} | {source:<8} | {fmt_time(stamp):<19} | {entry.rel}")
    print("-" * 78)
    times = [resolved_source(e, source) for e in entries] if source in TIME_SOURCES else []
    if times:
        duplicates = len(times) - len(set(times))
        print(
            f"{len(entries)} page(s), {fmt_time(min(times))} -> {fmt_time(max(times))}"
            + (f", {duplicates} timestamp tie(s) broken by file name" if duplicates else "")
        )
    else:
        print(f"{len(entries)} page(s)")


def print_skipped(skipped: list[tuple[Path, str]], limit: int = 20) -> None:
    if not skipped:
        return
    print(f"Skipped {len(skipped)} file(s):")
    for path, reason in skipped[:limit]:
        print(f"  {path.name}: {reason}")
    if len(skipped) > limit:
        print(f"  ... and {len(skipped) - limit} more")


def save_order_file(entries: list[ImageEntry], path: Path) -> None:
    lines = [
        "# Page order written by images_to_pdf_v2.py",
        "# Reuse with: --order-file this-file.txt",
        *[entry.rel for entry in entries],
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Order written: {path}")


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #
def images_to_pdf(target_dir: Path, output_dir: Path, output_name: str, args: argparse.Namespace) -> Path | None:
    target_dir = target_dir.resolve()
    output_dir = output_dir.resolve()

    if not target_dir.exists() or not target_dir.is_dir():
        raise SystemExit(f"Target directory does not exist: {target_dir}")

    entries, skipped = collect_images(target_dir, args)
    fatal_skips = [(path, reason) for path, reason in skipped if is_fatal_skip(reason)]
    if args.strict and fatal_skips:
        details = "\n".join(f"  {path.name}: {reason}" for path, reason in fatal_skips[:20])
        raise SystemExit(f"--strict: {len(fatal_skips)} image(s) could not be used:\n{details}")
    if not entries:
        raise SystemExit(
            f"No usable images found in: {target_dir}\n"
            "Supported extensions: " + ", ".join(sorted(IMAGE_EXTENSIONS))
        )

    ordered, source, note = order_entries(entries, args)
    if note:
        print(f"Ordering: {note}")

    if args.print_order or args.dry_run:
        print_plan(ordered, source)
    print_skipped(skipped)
    if args.dry_run:
        print("Dry run: no PDF written.")
        return None

    if not output_name.lower().endswith(".pdf"):
        output_name += ".pdf"

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise SystemExit(f"Output path exists and is not a folder: {output_dir}") from exc
    except PermissionError as exc:
        raise SystemExit(output_permission_hint(output_dir)) from exc

    output_pdf = output_dir / output_name
    # Build into a sibling temporary file and move it into place only once a
    # complete PDF exists: nothing that goes wrong during the build can then
    # truncate, replace or delete the previous good PDF.
    temp_pdf = output_pdf.with_name(f"{output_pdf.name}.{os.getpid()}.part")
    page_sizes: list[tuple[float, float]] = []
    failures: list[tuple[Path, str]] = []
    stats: dict = {"downscaled": 0}
    try:
        pdf = canvas.Canvas(
            str(temp_pdf),
            pagesize=PAGE_SIZES[args.page] or A4,
            pageCompression=1,
        )
        for entry in ordered:
            try:
                page_sizes.append(render_page(pdf, entry, args, stats))
            except Exception as exc:
                # Whatever a decoder or reportlab raises for one image may cost
                # that page, never the whole run; --strict opts into aborting.
                failures.append((entry.path, f"{exc.__class__.__name__}: {exc}"))
                if args.strict:
                    raise SystemExit(f"Failed to embed {entry.rel}: {exc}") from exc
                print(f"WARNING: skipped {entry.rel} ({exc.__class__.__name__}: {exc})")
        pdf.save()
    except BaseException as exc:
        discard_temp_pdf(temp_pdf)
        if isinstance(exc, SystemExit):
            raise
        if isinstance(exc, PermissionError):
            raise SystemExit(output_permission_hint(output_dir)) from exc
        if isinstance(exc, OSError):
            raise SystemExit(
                f"Could not write {temp_pdf}: {exc.strerror or exc}\n"
                "The previous file, if any, was left untouched."
            ) from exc
        raise
    finally:
        for temp_image in stats.get("temp_images", []):
            discard_temp_pdf(temp_image)

    pages = len(page_sizes)
    if pages == 0:
        discard_temp_pdf(temp_pdf)
        raise SystemExit("Every image failed to embed; no PDF written.")

    try:
        temp_pdf.replace(output_pdf)
    except PermissionError as exc:
        discard_temp_pdf(temp_pdf)
        raise SystemExit(output_pdf_locked_hint(output_pdf)) from exc
    except OSError as exc:
        discard_temp_pdf(temp_pdf)
        raise SystemExit(f"Could not replace {output_pdf}: {exc.strerror or exc}") from exc

    size_bytes = output_pdf.stat().st_size
    first = page_sizes[0]
    uniform = all(abs(w - first[0]) < 0.5 and abs(h - first[1]) < 0.5 for w, h in page_sizes)

    print(f"Created: {output_pdf}")
    print(f"Pages: {pages}   Size: {human_size(size_bytes)}   Order key: {source}")
    print(
        "Page size: "
        + (
            f"{first[0]:.1f} x {first[1]:.1f} pt (uniform)"
            if uniform
            else f"varies ({first[0]:.1f} x {first[1]:.1f} pt for page 1)"
        )
    )
    if not args.print_order and pages <= 12:
        print("Order: " + ", ".join(entry.name for entry in ordered))

    if failures:
        print(f"{len(failures)} image(s) failed to embed and were skipped.")
    if stats["downscaled"]:
        print(
            f"{stats['downscaled']} image(s) were downscaled to honour --max-dpi {args.max_dpi}"
            " (use --max-dpi 0 to keep full resolution)."
        )
    if args.save_order:
        save_order_file(ordered, Path(args.save_order))

    return output_pdf


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge images in a folder into one PDF. Images no longer need numeric names: "
            "ordering comes from EXIF capture time or file timestamps by default."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples", 1)[-1],
    )
    parser.add_argument("target_dir", nargs="?", default=".", help="Folder containing images (default: current folder).")
    parser.add_argument("-o", "--output-dir", help="Output folder (default: <target_dir>/output/pdf).")
    parser.add_argument("-n", "--name", default=DEFAULT_OUTPUT_NAME, help=f"Output PDF name (default: {DEFAULT_OUTPUT_NAME}).")
    parser.add_argument("--ask", action="store_true", help="Ask on the terminal for the image folder before running (used by the launcher when no folder was dropped on it).")

    ordering = parser.add_argument_group("ordering")
    ordering.add_argument("--sort", choices=ALL_SOURCES, default="auto", help="Ordering key (default: auto).")
    ordering.add_argument("--reverse", action="store_true", help="Reverse the final order.")
    ordering.add_argument("--order-file", help="Text file listing file names (or relative paths), one per line.")
    ordering.add_argument("--save-order", help="Write the resolved page order to this text file.")
    ordering.add_argument("--print-order", action="store_true", help="Print the page order and timestamp source, then build the PDF.")
    ordering.add_argument("--dry-run", action="store_true", help="Print the page order and exit without writing a PDF.")

    selection = parser.add_argument_group("selection")
    selection.add_argument("-r", "--recursive", action="store_true", help="Include images in sub-folders.")
    selection.add_argument("--min-side", type=int, default=0, metavar="PX", help="Skip images whose shorter side is below this many pixels.")
    selection.add_argument("--dedup", action="store_true", help="Skip byte-identical duplicate images (keeps the first by name).")
    selection.add_argument("--only-numbered", action="store_true", help="Legacy mode: only numeric file names, error on duplicate numbers.")
    selection.add_argument("--strict", action="store_true", help="Abort instead of skipping an image that fails to embed.")

    layout = parser.add_argument_group("page layout")
    layout.add_argument("--page", choices=tuple(PAGE_SIZES), default="a4", help="Page size: a4, letter, or fit (page follows each image).")
    layout.add_argument("--margin", type=float, default=0.0, metavar="MM", help="Page margin in millimetres (default: 0).")
    layout.add_argument("--dpi", type=int, default=150, help="Resolution assumed for --page fit (default: 150).")
    layout.add_argument("--max-dpi", type=int, default=200, metavar="DPI", help="Downscale images needing more than this resolution; 0 disables (default: 200).")
    layout.add_argument("--encode", choices=("keep", "jpeg", "png"), default="keep", help="Re-encoding format when a transform is required (default: keep).")
    layout.add_argument("--quality", type=int, default=90, help="JPEG quality for --encode jpeg/keep on JPEG sources (default: 90).")
    layout.add_argument("--grayscale", action="store_true", help="Convert pages to grayscale.")
    layout.add_argument(
        "--pdf-ascii85",
        choices=("off", "on"),
        default="off",
        help="Wrap every PDF stream in ASCII85 (reportlab's own default, roughly 20%% larger files); 'off' writes compact binary streams.",
    )
    return parser.parse_args(argv)


def output_permission_hint(path: Path) -> str:
    desktop_pdf = Path.home() / "Desktop" / "pdf"
    return (
        f"Cannot write here: {path}\n"
        "This location is not writable by the current (non-elevated) user.\n"
        "That is normal for folders which inherit a restrictive drive ACL, e.g. a folder on D:\\\n"
        "where only BUILTIN\\Administrators has write access; reads are usually still allowed.\n"
        "Options, best first:\n"
        f"  1. Write somewhere you own:  -o \"{desktop_pdf}\"\n"
        "  2. Grant yourself write access once (elevated PowerShell, that folder only):\n"
        "       icacls \"D:\\your-folder\" /grant *S-1-5-32-545:(OI)(CI)M /T\n"
        "  3. Run this command (or the .bat launcher) as administrator.\n"
    )


def output_pdf_locked_hint(path: Path) -> str:
    return (
        f"Cannot replace the existing PDF: {path}\n"
        "On Windows a PDF that is open in a viewer (or held by OneDrive, a sync\n"
        "client or an antivirus scanner) is locked against replacement.\n"
        "Nothing was lost: the file on disk is still the previous, complete PDF.\n"
        "Close the viewer and run again, or write a different file with -n.\n"
    )


def discard_temp_pdf(path: Path) -> None:
    """Remove a half-written temporary PDF. Never touches the real output file."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _clean_path_text(text: str) -> str:
    """Trim the things that ride along with a copied or pasted path.

    A byte-order mark or zero-width space turns "D:\\shots" into a *relative* path,
    because the drive letter is no longer the first character.
    """
    for junk in ("\ufeff", "\u200b", "\u200e", "\u200f"):
        text = text.replace(junk, "")
    return text.strip().strip('"').strip("'").strip()


def _decode_folder_answer(raw: bytes, preferred: str) -> str:
    """Decode a typed or piped folder path.

    Redirected input can arrive in a different encoding than the console code page
    (a UTF-8 pipeline feeding a CP936 process, for example), so try the plausible
    encodings and keep the first one that names something that actually exists.
    """
    candidates = [preferred, "utf-8", "gbk", "mbcs"]
    seen: set[str] = set()
    decoded: list[str] = []
    for encoding in candidates:
        if not encoding or encoding.lower() in seen:
            continue
        seen.add(encoding.lower())
        try:
            text = _clean_path_text(raw.decode(encoding))
        except (UnicodeDecodeError, LookupError):
            continue
        decoded.append(text)
        if text and Path(text).exists():
            return text
    if decoded:
        return decoded[0]
    return _clean_path_text(raw.decode("utf-8", "replace"))


def ask_for_folder(default: str) -> str:
    """Prompt on the terminal for the image folder.

    Asking here rather than in the .bat launcher keeps non-ASCII paths safe: Python
    talks to the Windows console through its Unicode layer, cmd.exe does not.
    """
    shown = Path(default).resolve()
    print("Drag a folder onto this launcher, or type/paste a path here.")
    print("Leave it empty to use the folder shown below.")
    print(f"Image folder [Enter = {shown}]: ", end="", flush=True)

    try:
        raw = sys.stdin.buffer.readline()
    except (AttributeError, OSError, ValueError, KeyboardInterrupt):
        raw = b""

    if not raw:
        try:
            answer = _clean_path_text(input())
        except (EOFError, KeyboardInterrupt):
            print()
            print("No folder typed; using the default folder.")
            return default
        return answer or default

    answer = _decode_folder_answer(raw, sys.stdin.encoding or "")
    if not answer:
        print("Empty answer; using the default folder.")
        return default
    return answer


def _configure_stdout() -> None:
    """Never crash while printing a file name the console code page cannot hold."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached console
                pass


def validate_args(args: argparse.Namespace) -> None:
    """Reject values that would otherwise build a silently broken PDF."""
    if not 1 <= args.quality <= 100:
        raise SystemExit("--quality must be between 1 and 100.")
    if args.dpi < 1:
        raise SystemExit("--dpi must be at least 1 (it is the assumed source resolution for --page fit).")
    if args.max_dpi < 0:
        raise SystemExit("--max-dpi must be 0 (disabled) or a positive number of dots per inch.")
    if args.margin < 0:
        raise SystemExit("--margin cannot be negative: it would draw the image outside its own page.")
    if args.page != "fit":
        page_short_side = min(PAGE_SIZES[args.page])
        if 2 * args.margin * MM >= page_short_side:
            limit = page_short_side / 2 / MM
            raise SystemExit(
                f"--margin {args.margin:g} mm leaves no room on a {args.page} page "
                f"(the short side is {page_short_side:.0f} pt, so the margin must stay below {limit:.0f} mm).\n"
                "Use --page fit (the page then grows with the margin) or a smaller margin."
            )
    if args.min_side < 0:
        raise SystemExit("--min-side cannot be negative.")
    name = args.name
    if not name.strip():
        raise SystemExit("-n/--name cannot be empty.")
    if Path(name).is_absolute() or any(part in name for part in ("/", "\\", ":")):
        raise SystemExit(f"-n/--name must be a plain file name, not a path: {name!r}")


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.dry_run and args.save_order:
        raise SystemExit("--save-order cannot be combined with --dry-run (no ordered file list is written).")
    validate_args(args)
    # reportlab wraps every stream in ASCII85 by default (roughly 25% larger
    # files).  It has to be set on the library config: Canvas accepts arbitrary
    # keywords and silently ignores an unknown `useA85`.
    rl_config.useA85 = args.pdf_ascii85 == "on"

    target_arg = ask_for_folder(args.target_dir) if args.ask else args.target_dir
    target_dir = Path(target_arg)
    output_dir = Path(args.output_dir) if args.output_dir else target_dir / "output" / "pdf"
    images_to_pdf(target_dir, output_dir, args.name, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
