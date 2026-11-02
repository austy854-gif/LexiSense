"""Upload checks that do not trust the browser-supplied Content-Type.

The client can label anything ``application/pdf`` or ``text/plain``. We accept
only a PDF that starts with the PDF magic bytes, or UTF-8 text with a ``.txt``
extension, and we store a sanitised extension so the object key cannot carry
path characters from the original filename.
"""
from __future__ import annotations

import re

PDF_MAGIC = b"%PDF"
ALLOWED_EXTENSIONS = {"pdf", "txt"}


class UploadRejected(ValueError):
    """The bytes are not a contract file we can store. ``str(self)`` is safe to show."""


def _extension(filename: str) -> str:
    if not filename or "." not in filename:
        return ""
    raw = filename.rsplit(".", 1)[-1].lower()
    return re.sub(r"[^a-z0-9]", "", raw)[:8]


def validate_upload(filename: str, content: bytes) -> tuple[str, str]:
    """Return ``(content_type, extension)`` or raise :class:`UploadRejected`."""
    if not content:
        raise UploadRejected("The file is empty.")
    ext = _extension(filename or "")
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejected("Invalid file type. Only PDF and TXT files are allowed.")
    if ext == "pdf":
        if not content.startswith(PDF_MAGIC):
            raise UploadRejected("That file is not a valid PDF.")
        return "application/pdf", "pdf"
    sample = content[:8192]
    if b"\x00" in sample:
        raise UploadRejected("That file is not plain text.")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise UploadRejected("Text files must be UTF-8.")
    return "text/plain", "txt"


def attachment_filename(original: str, extension: str) -> str:
    """A header-safe download name. ``extension`` is already allow-listed."""
    base = original or "contract"
    if "." in base:
        base = base.rsplit(".", 1)[0]
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", base)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_") or "contract"
    ext = extension if extension in ALLOWED_EXTENSIONS else "bin"
    name = f"{cleaned}.{ext}"
    return name[:120]
