"""PDF ingestion: extract, chunk and index the hotel information document.

The PDF is the source of truth for the chatbot. Text is extracted with pypdf,
split into traceable chunks that keep their heading and page number, and the
retrieval index is built from those chunks. The JSON file written under
data/index_cache/ is a derived cache only — deleting it simply forces a rebuild
from the PDF on the next start.

PDF content never determines room availability and never performs bookings;
those come from SQLite via booking.py.
"""
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader

import db

PDF_MAGIC = b"%PDF-"
MAX_PDF_BYTES = 10 * 1024 * 1024          # 10 MB is ample for a hotel handbook
MIN_EXTRACTED_CHARS = 40                   # below this the PDF has no usable text
SUPPLIED_PDF = Path(__file__).parent / "data" / "AROHAK_Hotel_Information_For_RAG.pdf"

# A numbered heading ("4. Room Categories") or a policy sub-heading
# ("Check-in Policy") starts a new chunk.
NUMBERED_HEADING = re.compile(r"^(\d{1,2})\.\s+([A-Z].{2,70})$")
SUB_HEADING = re.compile(r"^([A-Z][A-Za-z'\- ]{2,48}(?:Policy|Policies))$")
RUNNING_HEADER = re.compile(r"^(AROHAK Hackathon Hiring|Page \d+)$", re.IGNORECASE)
CAPACITY_LINE = re.compile(r"^(\d+)\s+guests?$", re.IGNORECASE)


class IngestionError(Exception):
    """Raised when a document cannot be accepted or produces no usable text."""


# ---------------------------------------------------------------- validation

def validate_pdf_bytes(data, original_name=""):
    """Check an uploaded document before it is allowed anywhere near disk."""
    if not data:
        raise IngestionError("No file was supplied.")
    if len(data) > MAX_PDF_BYTES:
        raise IngestionError(f"The file is larger than the {MAX_PDF_BYTES // (1024 * 1024)} MB limit.")
    if not data.startswith(PDF_MAGIC):
        raise IngestionError("That file is not a PDF.")
    if original_name and not safe_name(original_name).lower().endswith(".pdf"):
        raise IngestionError("The file must have a .pdf extension.")
    return True


def safe_name(name):
    """Reduce a client-supplied filename to a harmless basename.

    Any directory component is discarded, so "../../server.py" cannot escape
    the documents directory. The stored filename is derived from the hotel id
    regardless; this only sanitises what gets recorded and echoed back.
    """
    base = Path(str(name or "").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return cleaned[:120] or "document.pdf"


# ---------------------------------------------------------------- extraction

def extract_pages(pdf_path):
    """Return [(page_number, text)] for a PDF on disk."""
    try:
        reader = PdfReader(str(pdf_path))
        pages = [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, start=1)]
    except Exception as exc:
        raise IngestionError(f"The PDF could not be read: {exc}")
    if not pages:
        raise IngestionError("The PDF has no pages.")
    if sum(len(text) for _, text in pages) < MIN_EXTRACTED_CHARS:
        raise IngestionError(
            "No readable text could be extracted from that PDF. "
            "A scanned image PDF needs OCR before it can be used.")
    return pages


def chunk_pages(pages):
    """Split extracted pages into chunks that keep their heading and page.

    A chunk starts at a numbered heading ("3. Cancellation and Modification
    Policy") or a policy sub-heading ("Check-out Policy"), so every answer can
    be traced back to a named part of a specific page.
    """
    chunks = []
    current = None
    preamble = []
    last_numbered = None

    for page_number, text in pages:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or RUNNING_HEADER.match(line):
                continue

            numbered = NUMBERED_HEADING.match(line)
            sub = SUB_HEADING.match(line) if not numbered else None
            if numbered or sub:
                if current and current["lines"]:
                    chunks.append(current)
                if numbered:
                    heading = f"{numbered.group(1)}. {numbered.group(2).strip()}"
                    last_numbered = heading
                else:
                    # Qualify "Check-in Policy" with the section it sits under,
                    # whether or not that section had body text of its own.
                    label = sub.group(1).replace(" Policy", "").replace(" Policies", "")
                    heading = f"{last_numbered} - {label}" if last_numbered else sub.group(1)
                current = {"section": heading, "page": page_number, "lines": [],
                           "numbered": bool(numbered)}
                continue

            if current is None:
                preamble.append((page_number, line))
            else:
                current["lines"].append(line)

    if current and current["lines"]:
        chunks.append(current)

    if preamble:
        chunks.insert(0, {"section": "Document Information", "page": preamble[0][0],
                          "lines": [line for _, line in preamble], "numbered": False})

    built = []
    for chunk in chunks:
        text = normalise_lines(chunk["lines"])
        if len(text) < 25:
            continue
        built.append({"section": chunk["section"], "page": chunk["page"], "text": text})
    if not built:
        raise IngestionError("The PDF produced no usable sections.")
    return built


def normalise_lines(lines):
    """Join extracted lines into sentences.

    Bullet lines arrive without terminating punctuation, so a full stop is added
    where one is missing to keep sentence-level retrieval working.
    """
    parts = []
    for line in lines:
        # The document's bullet glyph comes out of pypdf as a control character.
        line = "".join(ch for ch in line if ch.isprintable() or ch.isspace())
        line = re.sub(r"^[•●\-\*–\s]+", "", line).strip()
        if not line:
            continue
        if not line.endswith((".", "!", "?", ":")):
            line += "."
        parts.append(line)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def room_capacities(chunks):
    """Read room names and capacities out of the extracted room table.

    pypdf renders the table one cell per line, so a line reading "2 guests" is
    preceded by the room name it belongs to.
    """
    capacities = {}
    for chunk in chunks:
        if "Room Categories" not in chunk["section"]:
            continue
        for match in re.finditer(r"([A-Z][A-Za-z ]{2,30}?)\.?\s+(\d+)\s+guests?\.?", chunk["text"]):
            name = match.group(1).strip().strip(".")
            name = re.sub(r"^(?:and|the|a)\s+", "", name, flags=re.IGNORECASE).strip()
            if name.lower() in ("room type", "capacity", "key features", "price night"):
                continue
            if 2 <= len(name) <= 30:
                capacities[name] = int(match.group(2))
    return capacities


# ---------------------------------------------------------------- documents

def documents_dir():
    path = Path(db.database_path()).parent / "documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir():
    path = Path(db.database_path()).parent / "index_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stored_pdf_path(hotel_id):
    return documents_dir() / f"{safe_name(hotel_id)}.pdf"


def cache_path(hotel_id):
    return cache_dir() / f"{safe_name(hotel_id)}.json"


def build_index(pdf_path):
    """Extract, chunk and index one PDF. Returns (chunks, page_count)."""
    pages = extract_pages(pdf_path)
    return chunk_pages(pages), len(pages)


def ingest_pdf_bytes(hotel_id, data, original_name="document.pdf"):
    """Validate, store and index an uploaded PDF for one hotel."""
    validate_pdf_bytes(data, original_name)
    target = stored_pdf_path(hotel_id)
    staging = target.with_suffix(".uploading")
    staging.write_bytes(data)
    try:
        chunks, page_count = build_index(staging)
    except IngestionError:
        staging.unlink(missing_ok=True)
        raise
    staging.replace(target)
    record = write_cache(hotel_id, chunks, target, page_count, safe_name(original_name))
    record_document(hotel_id, record)
    return record


def ensure_seed_document(hotel_id=db.DEFAULT_HOTEL_ID):
    """Put the supplied PDF in place for the seeded hotel on first run."""
    target = stored_pdf_path(hotel_id)
    if not target.exists():
        if not SUPPLIED_PDF.exists():
            raise IngestionError(f"The supplied hotel PDF is missing: {SUPPLIED_PDF}")
        shutil.copyfile(SUPPLIED_PDF, target)
    return target


def load_index(hotel_id=db.DEFAULT_HOTEL_ID, force=False):
    """Return the chunk list for a hotel, rebuilding from the PDF if needed."""
    pdf_path = ensure_seed_document(hotel_id)
    cache = cache_path(hotel_id)
    digest = file_digest(pdf_path)
    if cache.exists() and not force:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("source_sha256") == digest and cached.get("chunks"):
                return cached["chunks"]
        except (ValueError, OSError):
            pass
    chunks, page_count = build_index(pdf_path)
    record = write_cache(hotel_id, chunks, pdf_path, page_count, pdf_path.name)
    record_document(hotel_id, record)
    return chunks


def write_cache(hotel_id, chunks, pdf_path, page_count, original_name):
    record = {
        "hotel_id": hotel_id,
        "original_name": original_name,
        "pages": page_count,
        "chunks": chunks,
        "chunk_count": len(chunks),
        "source_sha256": file_digest(pdf_path),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": "Generated from the hotel PDF by ingest.py. Delete to force a rebuild.",
    }
    cache_path(hotel_id).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record_document(hotel_id, record):
    with db.connect(db.database_path()) as conn:
        conn.execute(
            """INSERT INTO hotel_documents (hotel_id, original_name, pages, chunk_count, sha256, uploaded_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(hotel_id) DO UPDATE SET
                   original_name=excluded.original_name, pages=excluded.pages,
                   chunk_count=excluded.chunk_count, sha256=excluded.sha256,
                   uploaded_at=excluded.uploaded_at""",
            (hotel_id, record["original_name"], record["pages"], record["chunk_count"],
             record["source_sha256"], record["generated_at"]),
        )


def document_info(hotel_id=db.DEFAULT_HOTEL_ID):
    with db.connect(db.database_path()) as conn:
        row = conn.execute("SELECT * FROM hotel_documents WHERE hotel_id=?", (hotel_id,)).fetchone()
    return dict(row) if row else None
