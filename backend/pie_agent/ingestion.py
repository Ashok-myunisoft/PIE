from __future__ import annotations
import hashlib, json, os, re, zipfile, tempfile
from pathlib import Path
from typing import Iterable
from .models import SourceRecord

TEXT_EXTS = {".txt",".md",".json",".csv",".cs",".ts",".html",".htm",".sql",".xml",".yaml",".yml",".py"}
DOC_EXTS = {".pdf",".xlsx",".pptx",".docx"}
IMAGE_EXTS = {".png",".jpg",".jpeg",".webp"}

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")

def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    r = PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in r.pages)

def _xlsx_text(path: Path) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    parts=[]
    for ws in wb.worksheets:
        parts.append(f"[SHEET:{ws.title}]")
        for row in ws.iter_rows(values_only=True):
            vals=[str(v) for v in row if v is not None]
            if vals: parts.append(" | ".join(vals))
    return "\n".join(parts)

def extract_text(path: Path) -> str:
    ext=path.suffix.lower()
    if ext in TEXT_EXTS: return _read_text(path)
    if ext == ".pdf": return _pdf_text(path)
    if ext == ".xlsx": return _xlsx_text(path)
    return ""

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

class IngestionResult:
    def __init__(self, root: Path, files: list[Path], records: list[SourceRecord], text: dict[str,str]):
        self.root=root; self.files=files; self.records=records; self.text=text

def _flatten_input(input_path: Path, work: Path) -> list[Path]:
    if input_path.is_dir():
        return [p for p in input_path.rglob("*") if p.is_file()]
    if input_path.suffix.lower()==".zip":
        out=work/"archive"
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(input_path) as z: z.extractall(out)
        files=[p for p in out.rglob("*") if p.is_file()]
        # Recursively extract nested ZIPs.
        nested=[p for p in files if p.suffix.lower()==".zip"]
        for nz in nested:
            target=nz.with_suffix("")
            target.mkdir(exist_ok=True)
            try:
                with zipfile.ZipFile(nz) as z: z.extractall(target)
            except zipfile.BadZipFile: pass
        return [p for p in out.rglob("*") if p.is_file()]
    return [input_path]

def ingest(input_path: str | Path, work_dir: str | Path | None = None) -> IngestionResult:
    input_path=Path(input_path).resolve()
    work=Path(work_dir or tempfile.mkdtemp(prefix="pie-ingest-")).resolve()
    files=_flatten_input(input_path, work)
    records=[]; text={}
    for p in files:
        rel=(p.relative_to(work).as_posix() if p.is_relative_to(work) else p.name)
        ext=p.suffix.lower()
        if ext==".zip": category="Other"
        elif ext in IMAGE_EXTS: category="Screen"
        elif ext==".pdf": category="Other"
        elif ext==".xlsx": category="Other"
        elif ext in {".cs",".ts",".py",".sql"}: category="Other"
        else: category="Other"
        content=extract_text(p)
        if content: text[rel]=content
        records.append(SourceRecord(
            fileName=rel, fileType=ext.lstrip(".") or "file", sourceCategory=category,
            sizeBytes=p.stat().st_size
        ))
    return IngestionResult(work, files, records, text)
