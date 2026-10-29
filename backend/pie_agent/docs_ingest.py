"""Human-written documents in the KMS (v0.25): user manuals, training material, brochures, SOPs.

Put files in data/docs (sub-folders named after a module, e.g. data/docs/inventory/, tag them
with that module) and run `docs-ingest`. PDF, Word (.docx), PowerPoint (.pptx), Excel, Markdown,
text and HTML are read without extra packages. Each document is split into ~1,500-character
passages (with page / slide numbers) and stored as `Document` points; re-ingesting a file
replaces its earlier passages.
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterator

from .paths import from_root

DOC_VERSION = "0.25.0"
DOC_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".html", ".htm", ".csv"}
CHUNK_CHARS = 1500


def _xml_text(xml: str, para_tag: str, text_tag: str) -> list[str]:
    paras = []
    for p in re.findall(rf"<{para_tag}[ >].*?</{para_tag}>", xml, re.S):
        t = "".join(re.findall(rf"<{text_tag}(?: [^>]*)?>([^<]*)</{text_tag}>", p))
        if t.strip():
            heading = re.search(r'w:pStyle w:val="(Heading\d|Title)"', p)
            paras.append(("# " if heading else "") + _unescape(t.strip()))
    return paras


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def read_document(path: Path) -> list[tuple[str, str]]:
    """[(location, text)]: page / slide / sheet / section and its text."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return [(f"page {i}", p.extract_text() or "") for i, p in enumerate(PdfReader(str(path)).pages, 1)]
    if ext == ".docx":
        with zipfile.ZipFile(path) as z:
            return [("", "\n\n".join(_xml_text(z.read("word/document.xml").decode("utf-8", "ignore"), "w:p", "w:t")))]
    if ext == ".pptx":
        with zipfile.ZipFile(path) as z:
            slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                            key=lambda n: int(re.search(r"(\d+)", n).group(1)))
            return [(f"slide {re.search(r'(\d+)', n).group(1)}", "\n\n".join(_xml_text(z.read(n).decode("utf-8", "ignore"), "a:p", "a:t")))
                    for n in slides]
    if ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        return [(f"sheet {ws.title}", "\n".join(" | ".join(str(v) for v in row if v is not None)
                                                for row in ws.iter_rows(values_only=True) if any(v is not None for v in row)))
                for ws in wb.worksheets]
    text = path.read_text(encoding="utf-8", errors="replace")
    if ext in {".html", ".htm"}:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(text, "html.parser").get_text("\n")
    return [("", text)]


def passages(sections: list[tuple[str, str]], size: int = CHUNK_CHARS) -> list[tuple[str, str]]:
    """Split at paragraph boundaries into ~size-character passages, keeping the nearest heading."""
    out = []
    for loc, text in sections:
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#)", re.sub(r"[ \t]+", " ", text or "")) if p.strip()]
        buf, heading = "", ""
        for p in paras:
            if p.startswith("#"):
                heading = p.lstrip("# ").strip()[:120]
            if buf and len(buf) + len(p) > size:
                out.append((loc, buf.strip()))
                buf = (f"[{heading}] " if heading else "")
            buf += p + "\n"
            while len(buf) > size * 1.6:
                out.append((loc, buf[:size].strip()))
                buf = buf[size:]
        if buf.strip():
            out.append((loc, buf.strip()))
    return [(loc, t) for loc, t in out if len(t) > 40]


def _known_modules() -> list[str]:
    root = from_root("outputs/modules")
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def guess_module(rel: str, text: str, modules: list[str]) -> str:
    parts = [p.lower() for p in Path(rel).parts[:-1]]
    for m in modules:
        if m in parts:
            return m
    low = f" {rel.lower()} {text[:20000].lower()} "
    counts = {m: len(re.findall(rf"\b{re.escape(m)}\b", low)) for m in modules if len(m) > 2}
    best = max(counts.items(), key=lambda kv: kv[1], default=("", 0))
    return best[0] if best[1] >= 3 else ""


def iter_documents(paths: list[str], include: list[str] | None = None) -> Iterator[tuple[Path, str]]:
    for raw in paths:
        p = from_root(raw)
        if p.is_file() and p.suffix.lower() == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix="pie-docs-"))
            with zipfile.ZipFile(p) as z:
                z.extractall(tmp)
            files = [(f, f"{p.name}/{f.relative_to(tmp).as_posix()}") for f in tmp.rglob("*") if f.is_file()]
        elif p.is_file():
            files = [(p, p.name)]
        elif p.is_dir():
            files = [(f, f.relative_to(p).as_posix()) for f in p.rglob("*") if f.is_file()]
        else:
            continue
        for f, rel in files:
            if f.suffix.lower() in DOC_EXTS and (not include or any(fnmatch.fnmatch(f.name.lower(), g.lower()) for g in include)):
                yield f, rel


def ingest_documents(kms: Any, paths: list[str], include: list[str] | None = None, module: str | None = None,
                     progress=print) -> dict[str, Any]:
    from .knowledge_ingest import _base_payload
    modules = _known_modules()
    kms.ensure_collection()
    docs, total, skipped = [], 0, []
    for f, rel in iter_documents(paths, include):
        try:
            sections = read_document(f)
        except Exception as exc:
            skipped.append(f"{rel}: {type(exc).__name__}")
            continue
        chunks = passages(sections)
        if not chunks:
            skipped.append(f"{rel}: no text (scanned image?)")
            continue
        mod = module or guess_module(rel, " ".join(t for _, t in sections), modules)
        sha = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        kms.delete({"must": [{"key": "knowledge_type", "match": {"value": "Document"}}, {"key": "source_file", "match": {"value": rel}}]})
        points = []
        for i, (loc, text) in enumerate(chunks):
            title = f"{Path(rel).stem}" + (f" ({loc})" if loc else "")
            points.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"pie://doc/{rel}/{i}")), "text": f"{title}\n{text}",
                           "payload": _base_payload(knowledge_id=f"DOC-{rel}#{i}", module=mod, knowledge_type="Document", title=title,
                                                    content=text, source_types=["Document"], generation_method="document",
                                                    knowledge_version=DOC_VERSION, source_file=rel, location=loc, source_hash=sha)})
        total += kms.upsert_points(points)
        docs.append({"file": rel, "module": mod, "passages": len(points)})
        progress(f"{rel}: {len(points)} passages (module {mod or '-'})")
    return {"status": "INGESTED", "collection": kms.collection, "documents": len(docs), "passages": total,
            "skipped": skipped, "files": docs}
