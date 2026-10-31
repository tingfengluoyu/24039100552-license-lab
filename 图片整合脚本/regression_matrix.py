"""Regression matrix for images_to_pdf_v2.py -- run before and after a change.

For every option combination it records:
  * exit code and the tail of stdout
  * number of pages and every /MediaBox
  * every embedded image XObject: /Filter chain, stored length, and the SHA-256
    of the *decoded pixel stream* (so two PDFs can be compared by content, not
    by encoding or file size)
  * output PDF size

Usage:
    python regression_matrix.py --out baseline.json
    python regression_matrix.py --out after.json --compare baseline.json
"""
import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = ROOT / "images_to_pdf_v2.py"
PY = sys.executable
FIXTURES = HERE / "fixtures"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def make_photo(path: Path) -> None:
    """Photographic: noise + gradient, so JPEG is the compact choice."""
    import random

    random.seed(7)
    img = Image.new("RGB", (900, 600))
    pixels = img.load()
    for y in range(600):
        for x in range(0, 900, 3):
            value = (x * 255) // 900
            noise = random.randint(-25, 25)
            pixels[x, y] = (max(0, min(255, value + noise)), (y * 255) // 600, 128)
        for x in range(1, 900, 3):
            pixels[x, y] = pixels[x - 1, y]
    img.save(path, format="JPEG", quality=88)


def make_shot(path, size=(1000, 700), bg="white") -> None:
    """Screenshot-like line art: flat areas, edges, text."""
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0], 60), fill=(240, 240, 245))
    draw.text((12, 20), "Lecture 3 - Photosynthesis", fill=(20, 20, 30))
    for i in range(6):
        draw.rectangle((30, 90 + i * 90, size[0] - 150, 150 + i * 90), outline=(90, 110, 140))
        draw.text((40, 105 + i * 90), f"note line {i}: chlorophyll absorbs red and blue light", fill=(10, 10, 10))
    img.save(path, format="PNG", optimize=True)


def make_rotated(path) -> None:
    """JPEG whose pixels are stored 600x900 but must display as 900x600."""
    img = Image.new("RGB", (600, 900), "white")
    draw = ImageDraw.Draw(img)
    for i in range(0, 900, 50):
        draw.line((0, i, 600, i), fill=(200, 40, 40), width=4)
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 CW on display
    img.save(path, format="JPEG", quality=90, exif=exif)


def make_gray(path) -> None:
    Image.new("L", (400, 300), 200).save(path, format="PNG")


def make_alpha(path) -> None:
    img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((20, 20, 280, 280), fill=(30, 160, 90, 180))
    img.save(path, format="PNG")


def build_fixtures() -> Path:
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    shots = FIXTURES / "shots"
    shots.mkdir(parents=True)
    make_photo(shots / "01_photo.jpg")
    make_shot(shots / "02_shot.png")
    make_shot(shots / "10_shot.png", bg=(250, 250, 240))
    make_rotated(shots / "03_rot.jpg")
    make_gray(shots / "04_gray.png")
    make_alpha(shots / "05_alpha.png")
    Image.new("RGB", (40, 30), "red").save(shots / "06_tiny.png")
    (shots / "07_broken.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    shutil.copyfile(shots / "02_shot.png", shots / "08_dup_of_shot.png")
    return shots


# --------------------------------------------------------------------------- #
# PDF inspection
# --------------------------------------------------------------------------- #
def decode_stream(head: bytes, stored: bytes):
    """Undo the /Filter chain of a PDF stream; returns (filters, raw bytes)."""
    filters = re.findall(rb"/(ASCII85Decode|FlateDecode|DCTDecode|A85|Fl|DCT)", head)
    names = []
    raw = stored
    for filt in filters:
        name = filt.decode()
        names.append({"A85": "ASCII85Decode", "Fl": "FlateDecode", "DCT": "DCTDecode"}.get(name, name))
        if name in ("ASCII85Decode", "A85"):
            text = raw.strip()
            text = text.removesuffix(b"~>").strip()
            raw = base64.a85decode(text)
        elif name in ("FlateDecode", "Fl"):
            raw = zlib.decompress(raw)
    return names, raw


def inspect_pdf(pdf: Path) -> dict:
    data = pdf.read_bytes()
    media = [tuple(round(float(v), 2) for v in m.split()) for m in re.findall(rb"/MediaBox\s*\[([^\]]+)\]", data)]
    images = []
    for match in re.finditer(rb"/Subtype\s*/Image(.{0,600}?)>>\s*stream\r?\n", data, re.S):
        # reportlab writes the dict as "... /Filter [...] /Height H /Length N
        # /Subtype /Image /Type /XObject /Width W >>", so the /Filter and /Length
        # entries sit *before* /Subtype and have to be read from a window that
        # reaches back; take the last hit so a previous object cannot win.
        before = data[max(0, match.start() - 500):match.start()]
        after = data[match.start():match.end()]
        lengths = re.findall(rb"/Length\s+(\d+)", before + after)
        filters = re.findall(rb"/Filter\s*(\[[^\]]*\]|/\w+)", before)
        if not lengths:
            continue
        length = int(lengths[-1])
        stored = data[match.end():match.end() + length]
        width = re.search(rb"/Width\s+(\d+)", after)
        height = re.search(rb"/Height\s+(\d+)", after)
        head = (filters[-1] if filters else b"") + after
        try:
            names, raw = decode_stream(head, stored)
            digest = hashlib.sha256(raw).hexdigest()[:16]
            decoded = len(raw)
        except Exception as exc:  # pragma: no cover - diagnostic only
            names, digest, decoded = [f"error:{type(exc).__name__}"], "-", -1
        images.append(
            {
                "filters": names,
                "stored": length,
                "decoded": decoded,
                "wh": [int(width.group(1)) if width else 0, int(height.group(1)) if height else 0],
                "sha": digest,
            }
        )
    return {
        "pdf_bytes": len(data),
        "pages": len(media),
        "mediaboxes": [list(m) for m in media],
        "images": images,
    }


# --------------------------------------------------------------------------- #
# cases
# --------------------------------------------------------------------------- #
CASES = [
    ("a4_default", []),
    ("letter_margin10", ["--page", "letter", "--margin", "10"]),
    ("fit_dpi200", ["--page", "fit", "--dpi", "200"]),
    ("fit_dpi200_maxdpi100", ["--page", "fit", "--dpi", "200", "--max-dpi", "100"]),
    ("keep_maxdpi0", ["--max-dpi", "0"]),
    ("encode_jpeg_gray", ["--encode", "jpeg", "--quality", "80", "--grayscale"]),
    ("encode_png_maxdpi80", ["--encode", "png", "--max-dpi", "80"]),
    ("min_side_dedup", ["--min-side", "100", "--dedup"]),
    ("sort_name_reverse", ["--sort", "name", "--reverse"]),
    ("a4_ascii85_on", ["--pdf-ascii85", "on"]),
]


def run_case(name: str, args: list[str], shots: Path, out_dir: Path) -> dict:
    target_out = out_dir / name
    cmd = [PY, str(SCRIPT), str(shots), "-o", str(target_out), "-n", f"{name}.pdf", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    result = {"args": args, "returncode": proc.returncode, "stdout_tail": proc.stdout.strip().splitlines()[-6:]}
    pdf = target_out / f"{name}.pdf"
    if pdf.exists():
        result.update(inspect_pdf(pdf))
    else:
        result["pdf_bytes"] = None
        result["pages"] = 0
        result["images"] = []
        result["mediaboxes"] = []
    return result


def run_strict_guard(shots: Path, out_dir: Path) -> dict:
    """A failing --strict run must not damage an existing good PDF."""
    target_out = out_dir / "strict_guard"
    target_out.mkdir(parents=True, exist_ok=True)
    pdf = target_out / "guard.pdf"
    # The existing PDF is deliberately built from a *different, smaller* source
    # set: a clobbering run cannot then coincidentally reproduce the same bytes.
    guard_src = out_dir / "guard_src"
    if guard_src.exists():
        shutil.rmtree(guard_src)
    guard_src.mkdir(parents=True)
    shutil.copyfile(shots / "01_photo.jpg", guard_src / "a.jpg")
    shutil.copyfile(shots / "02_shot.png", guard_src / "b.png")
    good_cmd = [PY, str(SCRIPT), str(guard_src), "-o", str(target_out), "-n", "guard.pdf"]
    subprocess.run(good_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    before = pdf.read_bytes() if pdf.exists() else b""
    # A truncated *JPEG* is the right probe: Pillow still reads its headers, so it
    # survives the scan and enters the page loop, but decoding fails while the page
    # is embedded -- exactly the mid-run failure --strict must abort on. (A
    # truncated PNG dies earlier, inside the scan, and never reaches the loop.)
    # Named to sort first, so the abort happens on page 1 and any partial write
    # shows up as a small clobbered file.
    whole = (shots / "01_photo.jpg").read_bytes()
    broken = shots / "00_broken_first.jpg"
    broken.write_bytes(whole[: int(len(whole) * 0.55)])
    strict_cmd = [PY, str(SCRIPT), str(shots), "-o", str(target_out), "-n", "guard.pdf", "--sort", "name", "--strict"]
    proc = subprocess.run(strict_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    broken.unlink()
    after = pdf.read_bytes() if pdf.exists() else b""
    return {
        "returncode": proc.returncode,
        "stderr_or_stdout_tail": (proc.stdout + proc.stderr).strip().splitlines()[-3:],
        "existing_pdf_before": len(before),
        "existing_pdf_after": len(after),
        "existing_pdf_untouched": before == after and len(before) > 0,
        "leftover_part_files": sorted(p.name for p in target_out.iterdir() if p.suffix == ".part"),
    }


def main() -> int:
    # The console here is CP936; a captured reportlab error message can contain
    # bytes that are not encodable, which must not kill the reporting step.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="matrix.json")
    parser.add_argument("--compare", help="previous matrix JSON to diff against")
    args = parser.parse_args()

    shots = build_fixtures()
    out_dir = HERE / "out"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    results = {"script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest()[:16], "cases": {}}
    for name, case_args in CASES:
        result = run_case(name, case_args, shots, out_dir)
        results["cases"][name] = result
        pages = result["pages"]
        size = result["pdf_bytes"]
        first = result["mediaboxes"][0] if result["mediaboxes"] else None
        filters = sorted({f for img in result["images"] for f in img["filters"]})
        print(f"{name:24s} rc={result['returncode']} pages={pages} pdf={size} box1={first} filters={','.join(filters)}")

    results["strict_guard"] = run_strict_guard(shots, out_dir)
    guard = results["strict_guard"]
    print(f"{'strict_guard':24s} rc={guard['returncode']} untouched={guard['existing_pdf_untouched']} "
          f"before={guard['existing_pdf_before']} after={guard['existing_pdf_after']} parts={guard['leftover_part_files']}")

    (HERE / args.out).write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nwrote {HERE / args.out}")

    if args.compare:
        old = json.loads((HERE / args.compare).read_text(encoding="utf-8"))
        print("\n=== diff vs " + args.compare + " ===")
        for name, new in results["cases"].items():
            old_case = old["cases"].get(name)
            if not old_case:
                print(f"{name}: new case")
                continue
            changes = []
            if old_case["pages"] != new["pages"]:
                changes.append(f"pages {old_case['pages']} -> {new['pages']}")
            if old_case["pdf_bytes"] != new["pdf_bytes"]:
                delta = (new["pdf_bytes"] or 0) - (old_case["pdf_bytes"] or 0)
                pct = 100.0 * delta / old_case["pdf_bytes"] if old_case["pdf_bytes"] else 0.0
                changes.append(f"bytes {old_case['pdf_bytes']} -> {new['pdf_bytes']} ({pct:+.1f}%)")
            if old_case["mediaboxes"] != new["mediaboxes"]:
                changes.append(f"mediabox {old_case['mediaboxes'][:2]} -> {new['mediaboxes'][:2]}")
            old_sha = [img["sha"] for img in old_case["images"]]
            new_sha = [img["sha"] for img in new["images"]]
            if old_sha != new_sha:
                changes.append(f"pixel streams differ ({len(old_sha)} vs {len(new_sha)} images)")
            print(f"{name:24s} " + ("; ".join(changes) if changes else "identical content and size"))
        if old.get("strict_guard") != results["strict_guard"]:
            print(f"strict_guard changed:\n  old={old.get('strict_guard')}\n  new={results['strict_guard']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
