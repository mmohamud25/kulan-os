"""Get the text out of a document, for "Summarise" in Files."""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

SUPPORTED = {
    "application/pdf", "text/plain", "text/markdown", "text/x-markdown", "text/html", "text/csv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text", "application/rtf", "text/rtf",
}
SUFFIXES = {".pdf", ".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".docx", ".odt", ".rtf", ".log"}
MAX_BYTES = 50 * 1024 * 1024  # bigger files are cut anyway; don't read them whole


class ExtractError(Exception):
    pass


def text_of(path: Path, limit: int = 200_000) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUFFIXES:
        raise ExtractError("Kulan Assistant can summarise PDFs, Word and LibreOffice documents, and text files.")
    if suffix == ".pdf":
        if shutil.which("pdftotext") is None:
            raise ExtractError("pdftotext (poppler-utils) is needed for PDFs.")
        out = _pdftotext(path)
        if out.returncode != 0:
            raise ExtractError("This PDF couldn't be read. It may be password-protected.")
        text = out.stdout.decode("utf-8", "replace")
        if not text.strip():
            raise ExtractError("This PDF has no text, so it may be scanned. Try Live Text on it.")
        return clean(text)[:limit]
    if suffix in (".docx", ".odt"):
        return clean(_zipped_xml(path, "word/document.xml" if suffix == ".docx" else "content.xml"))[:limit]
    with path.open("rb") as f:
        raw = f.read(MAX_BYTES).decode("utf-8", "replace")
    if suffix in (".html", ".htm"):
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        raw = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    elif suffix == ".rtf":
        raw = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw)
    return clean(raw)[:limit]


def _pdftotext(path: Path) -> subprocess.CompletedProcess:
    """pdftotext with no network and nothing of the home folder but this one file (a
    PDF from anywhere is untrusted input to a C parser). Without bubblewrap, or
    where it can't make a sandbox, it runs as before."""
    args = ["-layout", "-enc", "UTF-8", "-l", "60"]
    bwrap = shutil.which("bwrap")
    if bwrap:
        sandbox = [bwrap, "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
                   "--symlink", "usr/lib64", "/lib64", "--ro-bind-try", "/etc/fonts", "/etc/fonts",
                   "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--ro-bind", str(path), "/tmp/in.pdf",
                   "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--",
                   "/usr/bin/pdftotext", *args, "/tmp/in.pdf", "-"]
        out = subprocess.run(sandbox, capture_output=True, timeout=120, check=False)
        if not (out.returncode != 0 and out.stderr.startswith(b"bwrap:")):  # the sandbox itself failed: run as before
            return out
    return subprocess.run(["pdftotext", *args, str(path), "-"], capture_output=True, timeout=120, check=False)


def _zipped_xml(path: Path, member: str) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo(member)
            if info.file_size > MAX_BYTES:
                raise ExtractError("This document is too large to summarise.")
            root = ElementTree.fromstring(z.read(member))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ExtractError("This document couldn't be read.") from exc
    paragraphs = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in ("p", "h"):  # Word and OpenDocument paragraphs and headings
            text = "".join(element.itertext()).strip()
            if text:
                paragraphs.append(text)
    return "\n".join(paragraphs)


def clean(text: str) -> str:
    text = re.sub(r"[ \t\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
