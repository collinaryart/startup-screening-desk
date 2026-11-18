"""Uploaded documents: store them, describe them, and turn them into something a model can read and cite.

PDFs are sent to the model as they are (it reads the pages, including charts). Word, PowerPoint and Excel
files are converted to plain text here, with slide and sheet markers, and sent as text. Every document is
sent with citations switched on, so each fact comes back tied to a quoted passage.
"""
from __future__ import annotations

import base64
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from . import config

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

MAX_TEXT_CHARS = 600_000  # roughly 150k tokens; a larger document is refused, never silently cut


class DocError(ValueError):
    pass


def safe_name(name: str) -> str:
    base = Path(str(name).replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._ ()\-]", "_", base).strip(" .")
    return base[:120] or "document"


def kind_of(ext: str) -> str:
    ext = ext.lower()
    if ext in config.PDF_EXTS:
        return "pdf"
    if ext in config.OFFICE_EXTS:
        return "office"
    if ext in config.TEXT_EXTS:
        return "text"
    raise DocError(f"File type {ext or '(none)'} is not supported. Use PDF, Word, PowerPoint, Excel, text, Markdown or CSV.")


def store_upload(batch_id: str, startup_id: str, filename: str, data: bytes) -> dict[str, Any]:
    name = safe_name(filename)
    ext = Path(name).suffix.lower()
    kind = kind_of(ext)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise DocError(f"{name} is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise DocError(f"{name} is empty.")
    doc_id = uuid.uuid4().hex[:8]
    folder = config.UPLOAD_DIR / safe_name(batch_id) / safe_name(startup_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{doc_id}_{name}"
    path.write_bytes(data)
    meta: dict[str, Any] = {"id": doc_id, "name": name, "ext": ext, "kind": kind, "size": len(data),
                            "path": str(path.relative_to(config.UPLOAD_DIR)).replace("\\", "/")}
    try:
        if kind == "pdf":
            meta["pages"] = pdf_page_count(data)
        else:
            text = extract_text(path)
            meta["chars"] = len(text)
            if not text.strip():
                raise DocError(f"No text could be read from {name}.")
    except DocError:
        path.unlink(missing_ok=True)
        raise
    except Exception as e:  # a broken file should be refused at upload, not during a run
        path.unlink(missing_ok=True)
        raise DocError(f"{name} could not be read ({type(e).__name__}).") from e
    return meta


def resolve(meta: dict[str, Any]) -> Path:
    path = (config.UPLOAD_DIR / meta["path"]).resolve()
    if not path.is_relative_to(config.UPLOAD_DIR.resolve()):
        raise DocError("Document path is outside the uploads folder.")
    return path


def pdf_page_count(data: bytes) -> int | None:
    n = len(re.findall(rb"/Type\s*/Page(?![s\w])", data))
    return n or None


# ------------------------------------------------------------------ text extraction

def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in config.TEXT_EXTS:
        return path.read_bytes().decode("utf-8-sig", errors="replace")
    if ext == ".docx":
        return _docx_text(path)
    if ext == ".pptx":
        return _pptx_text(path)
    if ext == ".xlsx":
        return _xlsx_text(path)
    raise DocError(f"No text extraction for {ext}.")


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W_NS}body")
    lines: list[str] = []

    def para(p: ElementTree.Element) -> str:
        parts: list[str] = []
        for node in p.iter():
            if node.tag == f"{W_NS}t":
                parts.append(node.text or "")
            elif node.tag == f"{W_NS}tab":
                parts.append("\t")
            elif node.tag == f"{W_NS}br":
                parts.append("\n")
        return "".join(parts).strip()

    for child in list(body if body is not None else root):
        if child.tag == f"{W_NS}p":
            text = para(child)
            if text:
                lines.append(text)
        elif child.tag == f"{W_NS}tbl":
            for row in child.iter(f"{W_NS}tr"):
                cells = [" ".join(filter(None, (para(p) for p in cell.iter(f"{W_NS}p")))) for cell in row.iter(f"{W_NS}tc")]
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _pptx_text(path: Path) -> str:
    out: list[str] = []
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        for i, name in enumerate(slides, 1):
            root = ElementTree.fromstring(z.read(name))
            lines = []
            for p in root.iter(f"{A_NS}p"):
                text = "".join(t.text or "" for t in p.iter(f"{A_NS}t")).strip()
                if text:
                    lines.append(text)
            out.append(f"--- Slide {i} ---\n" + "\n".join(lines))
    return "\n\n".join(out)


def _xlsx_text(path: Path) -> str:
    import openpyxl  # imported here so the rest of the app runs without it

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out: list[str] = []
    try:
        for ws in wb.worksheets:
            lines = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    lines.append(" | ".join(cells))
            out.append(f"--- Sheet: {ws.title} ---\n" + "\n".join(lines))
    finally:
        wb.close()
    return "\n\n".join(out)


# ------------------------------------------------------------------ citable passages

MAX_PASSAGES = 1500


def _table_passages(rows: list[list[str]], where: str) -> list[str]:
    """Rows of a sheet as self-contained passages: each cell carries its column heading."""
    out: list[str] = []
    header: list[str] | None = None
    width = max((sum(1 for c in row if str(c).strip()) for row in rows), default=0)
    need = max(2, -(-width * 6 // 10))  # a heading row fills most of the columns; a title line does not
    for n, row in enumerate(rows, 1):
        cells = [c.strip() for c in row]
        filled = [c for c in cells if c]
        if not filled:
            continue
        if len(filled) == 1 or (header is None and len(filled) < need):
            out.append(f"{where}: " + ", ".join(filled))
        elif header is None:
            header = cells
            out.append(f"{where}, column headings: " + " | ".join(filled))
        else:
            parts = [f"{header[i]} = {c}" if i < len(header) and header[i] else c for i, c in enumerate(cells) if c]
            out.append(f"{where}, row {n}: " + "; ".join(parts))
    return out


def extract_passages(path: Path) -> list[str]:
    """A document as a list of short passages. A citation points at one passage, so a quote is one
    paragraph, one slide line or one spreadsheet row rather than a whole page."""
    ext = path.suffix.lower()
    if ext == ".xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        out: list[str] = []
        try:
            for ws in wb.worksheets:
                rows = [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
                out += _table_passages(rows, f"Sheet '{ws.title}'")
        finally:
            wb.close()
    elif ext in (".csv", ".tsv"):
        import csv
        import io

        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text), delimiter="\t" if ext == ".tsv" else ","))
        out = _table_passages(rows, "Table")
    elif ext == ".pptx":
        out = []
        for chunk in _pptx_text(path).split("\n\n"):
            lines = chunk.split("\n")
            slide = lines[0].strip("- ").strip()
            out += [f"{slide}: {line}" for line in lines[1:] if line.strip()]
    else:
        out = [line.strip() for line in extract_text(path).split("\n") if line.strip()]
    if len(out) > MAX_PASSAGES:  # a very long file: join neighbouring passages so the count stays manageable
        size = -(-len(out) // MAX_PASSAGES)
        out = ["\n".join(out[i:i + size]) for i in range(0, len(out), size)]
    return out


# ------------------------------------------------------------------ blocks for the model

def to_block(meta: dict[str, Any]) -> dict[str, Any]:
    """One document as an API content block, with citations on."""
    path = resolve(meta)
    if meta["kind"] == "pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        source: dict[str, Any] = {"type": "base64", "media_type": "application/pdf", "data": data}
    else:
        passages = extract_passages(path)
        if sum(len(p) for p in passages) > MAX_TEXT_CHARS:
            raise DocError(f"{meta['name']} is too long to read in one pass. Split it and upload the parts.")
        if not passages:
            raise DocError(f"No text could be read from {meta['name']}.")
        source = {"type": "content", "content": [{"type": "text", "text": p} for p in passages]}
    return {"type": "document", "source": source, "title": meta["name"], "citations": {"enabled": True}}


def clean_quote(text: str) -> str:
    """Quoted passages as people read them: no control characters, single spaces."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f­​]", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def describe(meta: dict[str, Any]) -> str:
    if meta.get("pages"):
        return f"{meta['name']} ({meta['pages']} page{'s' if meta['pages'] != 1 else ''})"
    if meta.get("chars"):
        return f"{meta['name']} ({meta['chars']:,} characters)"
    return meta["name"]
