#!/usr/bin/env python3
"""Self-test for images_to_pdf_v2.py.

Builds throwaway fixture folders under .selftest/ and checks the ordering
strategies, filters and page layouts against known-correct expectations.
Run it any time you change the script:

    python selftest_v2.py            # run and clean up
    python selftest_v2.py --keep     # keep .selftest/ for manual inspection

Exit code 0 = everything passed.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "images_to_pdf_v2.py"
ROOT = HERE / ".selftest"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail and not condition else ""))


def make_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path)


def run(*args: str, expect_ok: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if expect_ok and result.returncode != 0:
        print("---- stdout ----\n" + result.stdout)
        print("---- stderr ----\n" + result.stderr)
        raise SystemExit(f"command failed: {args}")
    return result


def pdf_page_boxes(path: Path) -> list[tuple[float, float]]:
    data = path.read_bytes()
    boxes = []
    for match in re.finditer(rb"/MediaBox\s*\[([^\]]+)\]", data):
        parts = match.group(1).split()
        if len(parts) == 4:
            try:
                x0, y0, x1, y1 = (float(p) for p in parts)
            except ValueError:
                continue
            boxes.append((round(x1 - x0, 1), round(y1 - y0, 1)))
    return boxes


def pdf_first_image_rgb(path: Path) -> bytes:
    """Decoded pixels of the first image XObject (handles ASCII85 and Flate)."""
    data = path.read_bytes()
    for match in re.finditer(rb"<<(.{0,4000}?)>>\s*stream\r?\n", data, re.S):
        head = match.group(1)
        if b"/Subtype /Image" not in head:
            continue
        length = int(re.search(rb"/Length (\d+)", head).group(1))
        payload = data[match.end() : match.end() + length]
        for name in re.findall(rb"/(ASCII85Decode|FlateDecode)", head):
            if name == b"ASCII85Decode":
                text = payload.strip()
                payload = base64.a85decode(text[:-2] if text.endswith(b"~>") else text)
            else:
                payload = zlib.decompress(payload)
        return payload
    return b""


def ordered_names(stdout: str) -> list[str]:
    """Parse the ``--print-order`` table: '   1 | created  | 2025-01-01 00:00:00 | name.png'."""
    names = []
    for line in stdout.splitlines():
        if line.count(" | ") != 3 or not line.strip()[:1].isdigit():
            continue
        names.append(line.split(" | ", 3)[3].strip())
    return names


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def build_fixtures() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)

    # 1. natural screenshots: creation times spread out, no EXIF
    shots = ROOT / "shots"
    shots.mkdir()
    for index in range(5):
        make_png(shots / f"clip_{index}.png", (600, 800), (index * 40, 80, 120))
        if index < 4:
            time.sleep(1.2)

    # 2. same images copied elsewhere: creation times collapse, mtimes survive
    copied = ROOT / "copied"
    copied.mkdir()
    for source in sorted(shots.iterdir()):
        shutil.copy2(source, copied / source.name)  # copy2 preserves mtime

    # 3. EXIF-tagged photos whose file times are shuffled versus capture time
    exif = ROOT / "exif"
    exif.mkdir()
    capture_order = ["c.png", "a.png", "d.png", "b.png"]
    for index, name in enumerate(capture_order):
        path = exif / name
        image = Image.new("RGB", (500, 400), (200, index * 50, 10))
        info = Image.Exif()
        info[36867] = f"2024:05:0{index + 1} 09:30:00"
        image.save(path, exif=info)
        time.sleep(0.05)

    # 4. legacy numbering plus junk, duplicates and a non-image file
    legacy = ROOT / "legacy"
    legacy.mkdir()
    make_png(legacy / "10.png", (300, 400), (10, 10, 10))
    make_png(legacy / "2.png", (300, 400), (20, 20, 20))
    make_png(legacy / "1.png", (300, 400), (30, 30, 30))
    make_png(legacy / "notes.png", (300, 400), (40, 40, 40))
    shutil.copy2(legacy / "1.png", legacy / "1 copy.png")
    shutil.copy2(legacy / "1.png", legacy / "1_dup.png")
    (legacy / "readme.txt").write_text("not an image", encoding="utf-8")
    (legacy / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really a png")
    make_png(legacy / "thumb.png", (40, 30), (0, 0, 0))

    # 5. nested folders for --recursive
    nested = ROOT / "nested" / "chapter2"
    nested.mkdir(parents=True)
    make_png(nested / "page_1.png", (400, 500), (5, 5, 5))
    make_png(ROOT / "nested" / "page_2.png", (400, 500), (6, 6, 6))


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def test_auto_prefers_creation_time() -> None:
    result = run(str(ROOT / "shots"), "--dry-run", "--print-order")
    names = ordered_names(result.stdout)
    check("auto/creation: chooses 'created'", "auto chose 'created'" in result.stdout, result.stdout)
    check("auto/creation: keeps capture order", names == [f"clip_{i}.png" for i in range(5)], str(names))


def test_auto_detects_batch_copy() -> None:
    result = run(str(ROOT / "copied"), "--dry-run", "--print-order")
    names = ordered_names(result.stdout)
    check("auto/copy: chooses 'modified'", "auto chose 'modified'" in result.stdout, result.stdout)
    check("auto/copy: keeps original capture order", names == [f"clip_{i}.png" for i in range(5)], str(names))


def test_exif_wins() -> None:
    result = run(str(ROOT / "exif"), "--dry-run", "--print-order")
    names = ordered_names(result.stdout)
    check("exif: chooses 'exif'", "auto chose 'exif'" in result.stdout, result.stdout)
    check("exif: orders by DateTimeOriginal", names == ["c.png", "a.png", "d.png", "b.png"], str(names))


def test_number_and_name_modes() -> None:
    result = run(str(ROOT / "legacy"), "--dry-run", "--sort", "number")
    names = ordered_names(result.stdout)
    check("number: numeric stems first, in value order", names[:3] == ["1.png", "2.png", "10.png"], str(names))
    check("number: non-numeric names sort last", bool(names) and names[-1] == "thumb.png", str(names))

    result = run(str(ROOT / "nested"), "--dry-run", "--sort", "name", "-r")
    names = ordered_names(result.stdout)
    check(
        "name: natural order, folder major",
        names == ["chapter2/page_1.png", "page_2.png"],
        str(names),
    )


def test_selection_filters() -> None:
    result = run(str(ROOT / "legacy"), "--dry-run", "--dedup", "--min-side", "100")
    names = ordered_names(result.stdout)
    check("dedup: identical copies removed", not any(n.startswith("1_dup") for n in names), str(names))
    check("min-side: tiny image skipped", "thumb.png" not in names, str(names))
    check("broken file reported", "broken.png" in result.stdout, result.stdout)
    check("min-side reported", "smaller than --min-side" in result.stdout, result.stdout)


def test_legacy_only_numbered() -> None:
    ok = run(str(ROOT / "legacy"), "--dry-run", "--sort", "number", "--only-numbered")
    check("only-numbered: non-numeric files skipped", ordered_names(ok.stdout) == ["1.png", "2.png", "10.png"], ok.stdout)

    duplicate_dir = ROOT / "dupnum"
    duplicate_dir.mkdir(exist_ok=True)
    make_png(duplicate_dir / "7.png", (100, 100), (1, 1, 1))
    make_png(duplicate_dir / "7.jpg", (100, 100), (2, 2, 2))
    bad = run(str(duplicate_dir), "--only-numbered", expect_ok=False)
    check("only-numbered: duplicate numbers abort", "Duplicate image numbers" in (bad.stdout + bad.stderr))


def test_pdf_output() -> None:
    out = ROOT / "out"
    result = run(str(ROOT / "shots"), "-o", str(out), "-n", "a4run")
    pdf = out / "a4run.pdf"
    boxes = pdf_page_boxes(pdf)
    check("pdf: a4 pages written", pdf.exists() and len(boxes) == 5, f"{pdf.exists()} {boxes}")
    check("pdf: a4 page size", boxes and abs(boxes[0][0] - 595.3) < 1 and abs(boxes[0][1] - 841.9) < 1, str(boxes[:1]))

    result = run(str(ROOT / "exif"), "-o", str(out), "-n", "fitrun", "--page", "fit", "--dpi", "100")
    fit_pdf = out / "fitrun.pdf"
    fit_boxes = pdf_page_boxes(fit_pdf)
    check("pdf: fit page count", len(fit_boxes) == 4, str(fit_boxes))
    check("pdf: fit page size follows the image", fit_boxes and abs(fit_boxes[0][0] - 360.0) < 1, str(fit_boxes[:1]))

    big = ROOT / "big"
    big.mkdir(exist_ok=True)
    make_png(big / "huge.png", (4000, 6000), (250, 250, 250))
    run(str(big), "-o", str(out), "-n", "cap")
    run(str(big), "-o", str(out), "-n", "nocap", "--max-dpi", "0")
    capped = (out / "cap.pdf").stat().st_size
    uncapped = (out / "nocap.pdf").stat().st_size
    check("max-dpi: shrinks a very large page", capped < uncapped, f"{capped} vs {uncapped}")

    run(str(big), "-o", str(out), "-n", "gray", "--grayscale", "--encode", "jpeg")
    check("grayscale+jpeg: pdf written", (out / "gray.pdf").stat().st_size > 1000)


def test_order_file() -> None:
    out = ROOT / "out"
    order = ROOT / "order.txt"
    run(str(ROOT / "shots"), "--dry-run", "--save-order", str(order), expect_ok=False)
    run(str(ROOT / "shots"), "-o", str(out), "-n", "seed", "--save-order", str(order))
    check("order-file: written by --save-order", order.exists() and "clip_0.png" in order.read_text(encoding="utf-8"))

    flipped = ROOT / "order_flipped.txt"
    names = [ln for ln in order.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    flipped.write_text("\n".join(reversed(names)) + "\n", encoding="utf-8")
    result = run(str(ROOT / "shots"), "--dry-run", "--print-order", "--order-file", str(flipped))
    got = ordered_names(result.stdout)
    check("order-file: overrides the sort key", got == list(reversed(names)), f"{got} vs {list(reversed(names))}")


def test_cli_guards() -> None:
    result = run(str(ROOT / "shots"), "--dry-run", "--save-order", str(ROOT / "x.txt"), expect_ok=False)
    check("cli: --dry-run + --save-order rejected", "--save-order" in (result.stdout + result.stderr))
    result = run(str(ROOT / "shots"), "--dry-run", "--quality", "0", expect_ok=False)
    check("cli: bad quality rejected", "quality" in (result.stdout + result.stderr))
    result = run(str(ROOT / "missing-dir"), expect_ok=False)
    check("cli: missing folder rejected", "does not exist" in (result.stdout + result.stderr))


def load_script_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("images_to_pdf_v2_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks the module up in sys.modules while the class body runs.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def test_permission_guidance() -> None:
    """An unwritable output location must explain the options, not traceback."""
    import pathlib

    module = load_script_module()
    shots = ROOT / "shots"
    out_dir = ROOT / "nowrite" / "pdf"
    out_name = "merged.pdf"
    args = module.parse_args([str(shots)])

    original_mkdir = pathlib.Path.mkdir

    def denied(self, *a, **k):
        raise PermissionError(13, "Access is denied")

    pathlib.Path.mkdir = denied
    try:
        try:
            module.images_to_pdf(shots, out_dir, out_name, args)
        except SystemExit as exc:
            message = str(exc)
        else:
            message = ""
    finally:
        pathlib.Path.mkdir = original_mkdir
    check(
        "permission: unwritable output dir explains the options",
        "Cannot write here" in message and "-o" in message and "icacls" in message,
        message[:140],
    )

    original_canvas = module.canvas.Canvas

    class DeniedCanvas:
        def __init__(self, *a, **k):
            raise PermissionError(13, "Access is denied")

    module.canvas.Canvas = DeniedCanvas
    try:
        try:
            module.images_to_pdf(shots, out_dir, out_name, args)
        except SystemExit as exc:
            canvas_message = str(exc)
        else:
            canvas_message = ""
    finally:
        module.canvas.Canvas = original_canvas
    check(
        "permission: denied PDF write explains the options and leaves no partial file",
        "Cannot write here" in canvas_message and not (out_dir / out_name).exists(),
        canvas_message[:140],
    )


def test_ask_folder_decoding() -> None:
    """--ask must accept a path whatever code page the bytes arrived in."""
    module = load_script_module()
    shots = ROOT / "shots"
    as_utf8 = str(shots).encode("utf-8") + b"\r\n"
    as_gbk = str(shots).encode("gbk", "replace") + b"\r\n"

    check(
        "ask: utf-8 bytes resolve when the process assumes cp936",
        module._decode_folder_answer(as_utf8, "cp936") == str(shots),
        module._decode_folder_answer(as_utf8, "cp936"),
    )
    check(
        "ask: gbk bytes resolve when the process assumes utf-8",
        module._decode_folder_answer(as_gbk, "utf-8") == str(shots),
        module._decode_folder_answer(as_gbk, "utf-8"),
    )
    check(
        "ask: a path that really is missing is returned as typed",
        module._decode_folder_answer(b"C:\\no\\such\\folder\r\n", "cp936") == "C:\\no\\such\\folder",
    )
    check(
        "ask: a byte-order mark does not turn an absolute path relative",
        module._decode_folder_answer(b"\xef\xbb\xbf" + as_utf8, "utf-8") == str(shots),
        module._decode_folder_answer(b"\xef\xbb\xbf" + as_utf8, "utf-8"),
    )


def test_atomic_output() -> None:
    """A write failure must never damage the PDF that is already on disk."""
    import pathlib

    module = load_script_module()
    shots = ROOT / "shots"
    out_dir = ROOT / "atomic"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = "keep.pdf"
    output = out_dir / out_name
    output.write_bytes(b"%PDF-1.4\nprevious good file\n")
    before = output.read_bytes()
    args = module.parse_args([str(shots), "-o", str(out_dir), "-n", out_name])

    original_canvas = module.canvas.Canvas

    class FullDiskCanvas:
        """Writes a partial file, then fails the way a full disk does."""

        def __init__(self, target, *a, **k):
            Path(target).write_bytes(b"%PDF-1.4\nhalf written\n")

        def setPageSize(self, *a, **k) -> None:  # noqa: N802 - reportlab's name
            pass

        def drawImage(self, *a, **k) -> None:  # noqa: N802 - reportlab's name
            pass

        def showPage(self, *a, **k) -> None:  # noqa: N802 - reportlab's name
            pass

        def save(self) -> None:
            raise OSError(28, "No space left on device")

    def locked_replace(self, target):  # noqa: ANN001 - patched Path method
        raise PermissionError(13, "Access is denied")

    module.canvas.Canvas = FullDiskCanvas
    try:
        try:
            module.images_to_pdf(shots, out_dir, out_name, args)
        except SystemExit as exc:
            disk_message = str(exc)
        else:
            disk_message = "(no SystemExit)"
    finally:
        module.canvas.Canvas = original_canvas
    check(
        "atomic: a full disk keeps the previous PDF and leaves no .part file",
        output.read_bytes() == before
        and "Could not write" in disk_message
        and not list(out_dir.glob("*.part")),
        f"{disk_message[:100]} | parts={sorted(p.name for p in out_dir.glob('*.part'))}",
    )

    original_replace = pathlib.Path.replace
    pathlib.Path.replace = locked_replace
    try:
        try:
            module.images_to_pdf(shots, out_dir, out_name, args)
        except SystemExit as exc:
            locked_message = str(exc)
        else:
            locked_message = "(no SystemExit)"
    finally:
        pathlib.Path.replace = original_replace
    check(
        "atomic: a locked target PDF is reported as locked, not as an ACL problem",
        output.read_bytes() == before
        and "Cannot replace the existing PDF" in locked_message
        and "icacls" not in locked_message
        and not list(out_dir.glob("*.part")),
        locked_message[:140],
    )


def test_layout_bounds() -> None:
    """Page geometry must fit the page, while still filling a normal one."""
    module = load_script_module()
    prepared = module.Prepared(reader=None, width=1, height=1)
    _, x, y, draw_w, draw_h = module.page_geometry(prepared, (0.48, 0.48), 0.0)
    check(
        "layout: a sub-1 pt page shrinks the drawing instead of overflowing it",
        abs(draw_w - 0.48) < 1e-9 and abs(draw_h - 0.48) < 1e-9 and abs(x) < 1e-9 and abs(y) < 1e-9,
        f"{draw_w}x{draw_h} at {x},{y}",
    )
    small = module.Prepared(reader=None, width=40, height=30)
    _, _, _, draw_w, draw_h = module.page_geometry(small, (595.2756, 841.8898), 0.0)
    check(
        "layout: a small image is still blown up to fill the page",
        abs(draw_w - 595.2756) < 0.01 and abs(draw_h - 30 * (595.2756 / 40)) < 0.01,
        f"{draw_w}x{draw_h}",
    )


def test_transparency_flattening() -> None:
    """A transparent PNG must composite onto white, not keep its hidden colour."""
    module = load_script_module()
    shots = ROOT / "alphas"
    shots.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (4, 4), (255, 0, 0, 0))  # transparent, red underneath
    for x in range(2, 4):
        for y in range(4):
            image.putpixel((x, y), (0, 0, 255, 255))
    path = shots / "alpha.png"
    image.save(path)

    check("alpha: RGBA is detected as transparent", module.image_has_alpha(image))
    check("alpha: plain RGB is not", not module.image_has_alpha(Image.new("RGB", (2, 2), "white")))
    palette = Image.new("P", (2, 2))
    palette.info["transparency"] = 0
    check("alpha: palette transparency is detected", module.image_has_alpha(palette))

    flat = module.flatten_alpha(image)
    check(
        "alpha: colour hidden under a transparent pixel becomes white",
        flat.getpixel((0, 0)) == (255, 255, 255) and flat.getpixel((3, 0)) == (0, 0, 255),
        f"{flat.getpixel((0, 0))} / {flat.getpixel((3, 0))}",
    )
    check("alpha: scan_image records the flag", module.scan_image(path, path.name).has_alpha is True)

    out = ROOT / "alpha_out"
    run(str(shots), "-o", str(out), "--max-dpi", "0")
    pixels = pdf_first_image_rgb(out / "merged_images.pdf")
    check(
        "alpha: the PDF stores white where the PNG was transparent",
        pixels[:3] == b"\xff\xff\xff" and pixels[9:12] == b"\x00\x00\xff",
        f"transparent={pixels[:3]!r} opaque={pixels[9:12]!r}",
    )


def test_fit_page_ignores_max_dpi() -> None:
    """--page fit derives the page from the source pixels, not from --max-dpi."""
    shots = ROOT / "fitshots"
    shots.mkdir(parents=True, exist_ok=True)
    make_png(shots / "big.png", (900, 600), (10, 20, 30))
    run(str(shots), "--page", "fit", "--dpi", "200", "--max-dpi", "0", "-o", str(ROOT / "fit_plain"), "-n", "a.pdf")
    run(str(shots), "--page", "fit", "--dpi", "200", "--max-dpi", "100", "-o", str(ROOT / "fit_capped"), "-n", "b.pdf")
    plain = pdf_page_boxes(ROOT / "fit_plain" / "a.pdf")
    capped = pdf_page_boxes(ROOT / "fit_capped" / "b.pdf")
    check(
        "layout: --max-dpi lowers resolution without shrinking a fit page",
        bool(plain) and plain == capped and abs(plain[0][0] - 900 / 200 * 72) < 0.5,
        f"plain={plain} capped={capped}",
    )


def test_cli_validation() -> None:
    cases = [
        (["--margin", "-1"], "cannot be negative"),
        (["--margin", "200"], "leaves no room"),
        (["--margin", "200", "--page", "fit"], ""),
        (["--dpi", "0"], "--dpi must be at least 1"),
        (["--max-dpi", "-5"], "--max-dpi must be 0"),
        (["--min-side", "-1"], "--min-side cannot be negative"),
        (["-n", "sub/dir.pdf"], "plain file name"),
        (["-n", "C:\\abs.pdf"], "plain file name"),
        (["-n", "   "], "cannot be empty"),
    ]
    for extra, needle in cases:
        result = run(str(ROOT / "shots"), "--dry-run", *extra, expect_ok=not needle)
        output = result.stdout + result.stderr
        check(
            f"cli: {'accepts' if not needle else 'rejects'} {' '.join(extra)}",
            (needle in output) if needle else True,
            output.strip()[:140],
        )
    out_file = ROOT / "not_a_folder.pdf"
    out_file.write_bytes(b"not a folder")
    result = run(str(ROOT / "shots"), "-o", str(out_file), expect_ok=False)
    check(
        "cli: -o pointing at an existing file is rejected",
        "not a folder" in (result.stdout + result.stderr),
        (result.stdout + result.stderr).strip()[:140],
    )


def test_unsupported_files_are_reported() -> None:
    folder = ROOT / "unsupported"
    folder.mkdir(exist_ok=True)
    make_png(folder / "1.png", (40, 30), (1, 2, 3))
    shutil.copy2(folder / "1.png", folder / "screenshot_no_ext")
    (folder / "notes.txt").write_text("just text", encoding="utf-8")
    result = run(str(folder), "-o", str(ROOT / "out_unsupported"), "--dry-run")
    check(
        "selection: an image without a usable extension is reported instead of vanishing",
        "screenshot_no_ext" in result.stdout and "unsupported file type" in result.stdout,
        result.stdout,
    )
    check(
        "selection: an ordinary text file is not reported as a skipped image",
        "notes.txt" not in result.stdout,
        result.stdout,
    )


def test_strict_covers_scan_skips() -> None:
    folder = ROOT / "strict_scan"
    folder.mkdir(exist_ok=True)
    make_png(folder / "good.png", (40, 30), (4, 5, 6))
    (folder / "broken.jpg").write_bytes(b"")
    loose = run(str(folder), "-o", str(ROOT / "out_strict_loose"))
    strict = run(str(folder), "-o", str(ROOT / "out_strict"), "--strict", expect_ok=False)
    check(
        "strict: without --strict an unusable file is only reported",
        loose.returncode == 0 and "broken.jpg" in loose.stdout,
        loose.stdout,
    )
    check(
        "strict: --strict also aborts on a file that failed before embedding",
        strict.returncode == 1 and "could not be used" in (strict.stdout + strict.stderr),
        strict.stdout + strict.stderr,
    )


def test_junction_loop_is_pruned() -> None:
    folder = ROOT / "junction"
    (folder / "sub").mkdir(parents=True, exist_ok=True)
    make_png(folder / "top.png", (40, 30), (9, 9, 9))
    make_png(folder / "sub" / "nested.png", (40, 30), (8, 8, 8))
    link = folder / "loop"
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(folder)],
        capture_output=True,
        text=True,
    )
    try:
        if made.returncode != 0:
            check("selection: junction loop does not duplicate pages (mklink unavailable)", True)
            return
        result = run(str(folder), "-o", str(ROOT / "out_junction"), "-r", "--print-order")
        names = ordered_names(result.stdout)
        check(
            "selection: a junction pointing back at its own folder does not duplicate pages",
            sorted(names) == ["sub/nested.png", "top.png"],
            str(names),
        )
    finally:
        if link.exists():
            try:
                os.rmdir(link)  # removes the junction itself, never the target
            except OSError:
                pass


def test_jpeg_uses_dct_stream_and_cleans_up() -> None:
    folder = ROOT / "jpeg_temp"
    folder.mkdir(exist_ok=True)
    for index in range(3):
        make_png(folder / f"shot{index}.png", (300, 200), (index * 30, 60, 90))
    temp_dir = Path(tempfile.gettempdir())
    before = set(temp_dir.glob("images_to_pdf_*.jpg"))
    run(str(folder), "-o", str(ROOT / "out_jtemp"), "--encode", "jpeg", "--max-dpi", "0")
    leftover = set(temp_dir.glob("images_to_pdf_*.jpg")) - before
    check("encode: --encode jpeg leaves no temporary file behind", not leftover, str(leftover))
    check(
        "encode: --encode jpeg embeds the JPEG stream directly",
        b"/DCTDecode" in (ROOT / "out_jtemp" / "merged_images.pdf").read_bytes(),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the .selftest fixture folder")
    options = parser.parse_args()

    print(f"Building fixtures in {ROOT} ...")
    build_fixtures()

    test_auto_prefers_creation_time()
    test_auto_detects_batch_copy()
    test_exif_wins()
    test_number_and_name_modes()
    test_selection_filters()
    test_legacy_only_numbered()
    test_pdf_output()
    test_order_file()
    test_cli_guards()
    test_permission_guidance()
    test_ask_folder_decoding()
    test_atomic_output()
    test_layout_bounds()
    test_transparency_flattening()
    test_fit_page_ignores_max_dpi()
    test_cli_validation()
    test_unsupported_files_are_reported()
    test_strict_covers_scan_skips()
    test_junction_loop_is_pruned()
    test_jpeg_uses_dct_stream_and_cleans_up()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  failed: {name}")

    if not options.keep:
        shutil.rmtree(ROOT, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
