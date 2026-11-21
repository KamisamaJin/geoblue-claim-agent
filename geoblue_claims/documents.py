"""Prepare immutable originals for Codex. Never interpret medical facts here."""

import re
import shutil
import subprocess
from pathlib import Path

from .common import ClaimError, digest, private_dir, read_json, write_json

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif"}
MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES = 100


def file_bytes(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ClaimError("Expected a regular, non-symlink file: " + str(path))
    before = path.stat()
    if before.st_size == 0 or before.st_size > MAX_BYTES:
        raise ClaimError("File must be nonempty and at most 100 MiB: " + str(path))
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ClaimError("File changed while being read; retry after copying finishes: " + str(path))
    return data


def run_tool(name, arguments):
    executable = shutil.which(name)
    if not executable:
        hint = "Install Poppler (brew install poppler)." if name.startswith("pdf") else "HEIC conversion requires macOS sips."
        raise ClaimError("Missing " + name + ". " + hint)
    try:
        result = subprocess.run([executable] + [str(x) for x in arguments], capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClaimError(name + " failed or timed out; original retained") from exc
    if result.returncode:
        # Parser output can contain document contents. Do not copy it into generic error logs.
        if name == "sips":
            raise ClaimError("macOS image conversion failed; check file decoding and sandbox access to native codecs; original retained")
        raise ClaimError(name + " could not process this file (possibly damaged or password protected); original retained")
    return result.stdout.decode("utf-8", errors="replace")


def prepare(original, expected_hash):
    original = Path(original)
    if digest(file_bytes(original)) != expected_hash:
        raise ClaimError("Stored original changed; restore the original before preparing or submitting")
    folder = private_dir(original.parent / "prepared")
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        cached = read_json(manifest_path)
        paths = [cached.get("text_path")] + [p["path"] for p in cached.get("pages", [])]
        hashes = cached.get("output_hashes", {})
        if (cached.get("version") == 1 and cached.get("sha256") == expected_hash
                and cached.get("page_count", 0) > 0 and paths and all(
                    p and Path(p).is_file() and hashes.get(p) == digest(Path(p).read_bytes()) for p in paths)):
            return cached
    suffix = original.suffix.lower()
    text_path = folder / "text.txt"
    pages = []
    if suffix == ".pdf":
        info = run_tool("pdfinfo", [original])
        count = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
        if not count:
            raise ClaimError("Could not determine PDF page count")
        page_count = int(count.group(1))
        if not 1 <= page_count <= MAX_PAGES:
            raise ClaimError("PDF exceeds the 100-page preparation limit; split it into smaller documents")
        run_tool("pdftotext", ["-layout", "-enc", "UTF-8", original, text_path])
        for number in range(1, page_count + 1):
            prefix = folder / ("page-%04d" % number)
            run_tool("pdftoppm", ["-f", number, "-l", number, "-r", 120, "-singlefile", "-png", original, prefix])
            pages.append({"page": number, "path": str(prefix) + ".png"})
    elif suffix in {".heic", ".heif"}:
        output = folder / "page-0001.png"
        run_tool("sips", ["-s", "format", "png", original, "--out", output])
        text_path.write_text("", encoding="utf-8")
        pages.append({"page": 1, "path": str(output)})
    elif suffix in {".png", ".jpg", ".jpeg"}:
        content = original.read_bytes()
        if not (content.startswith(b"\x89PNG\r\n\x1a\n") if suffix == ".png" else content.startswith(b"\xff\xd8\xff")):
            raise ClaimError("Image contents do not match the file format")
        if shutil.which("sips"):
            info = run_tool("sips", ["-g", "pixelWidth", "-g", "pixelHeight", original])
            dimensions = re.findall(r"pixel(?:Width|Height):\s+(\d+)", info)
            if len(dimensions) != 2 or not all(int(x) > 0 for x in dimensions):
                raise ClaimError("Image could not be decoded")
        text_path.write_text("", encoding="utf-8")
        pages.append({"page": 1, "path": str(original)})
    else:
        raise ClaimError("Unsupported format: " + suffix)
    for p in [text_path] + [Path(x["path"]) for x in pages]:
        if not p.is_file() or (p != text_path and p.stat().st_size == 0):
            raise ClaimError("Document preparation produced missing or empty page images")
        p.chmod(0o600)
    manifest = {"version": 1, "sha256": expected_hash, "page_count": len(pages),
                "text_path": str(text_path), "pages": pages}
    manifest["output_hashes"] = {str(p): digest(p.read_bytes()) for p in [text_path] + [Path(x["path"]) for x in pages]}
    write_json(manifest_path, manifest)
    return manifest
