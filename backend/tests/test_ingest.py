import pytest

from app.db import DocumentRow
from app.ingest.chunker import chunk_id, chunk_pages
from app.ingest.loaders import (
    PageText,
    UnsupportedFileError,
    detect_kind,
    load_pdf,
    load_text,
)
from app.ingest.pipeline import ingest_file, reindex_all, safe_name
from app.providers.memory_store import MemoryVectorStore
from tests.conftest import hash_embed


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal valid PDF with one text line per page (no external libs)."""
    objs: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + 2 * n
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_detect_kind():
    assert detect_kind("a.PDF") == "pdf"
    assert detect_kind("notes.md") == "text"
    assert detect_kind("pic.JPG") == "image"
    with pytest.raises(UnsupportedFileError):
        detect_kind("virus.exe")


def test_safe_name_strips_directories():
    assert safe_name("../../etc/passwd.txt") == "passwd.txt"
    with pytest.raises(UnsupportedFileError):
        safe_name("")
    with pytest.raises(UnsupportedFileError):
        safe_name(".hidden")


def test_load_pdf_pages():
    pages = load_pdf(make_pdf(["Hello page one", "Second page text"]))
    assert [p.page for p in pages] == [1, 2]
    assert "Hello page one" in pages[0].text


def test_load_text_empty():
    assert load_text(b"   \n") == []
    assert load_text("héllo".encode())[0].text == "héllo"


def test_chunking_overlap_ids_and_pages():
    long = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_pages([PageText(page=3, text=long)], "doc.pdf", size=200, overlap=40)
    assert len(chunks) > 3
    assert all(len(c.text) <= 200 for c in chunks)
    assert {c.page for c in chunks} == {3}
    assert len({c.id for c in chunks}) == len(chunks)
    assert chunks[0].id == chunk_id("doc.pdf", 3, 0)  # deterministic
    assert chunk_pages([], "x.txt") == []


async def test_ingest_txt_then_retrieve(llm, store, db, settings):
    doc = await ingest_file(
        llm=llm, store=store, db=db, settings=settings, filename="policy.txt",
        data=b"Annual leave is 24 days.\n\nRemote work is allowed three days a week.",
        uploaded_by="bob",
    )
    assert doc.kind == "text" and doc.chunks >= 1
    assert [d.name for d in await db.list_documents()] == ["policy.txt"]
    assert (settings.upload_dir and __import__("pathlib").Path(settings.upload_dir, "policy.txt").exists())
    hits = await store.search(hash_embed("how many days of annual leave"), k=2)
    assert hits and "leave" in hits[0].chunk.text.lower()
    assert hits[0].chunk.source == "policy.txt"


async def test_ingest_pdf_keeps_page_numbers(llm, store, db, settings):
    data = make_pdf(["Cats sleep a lot", "Rockets burn fuel quickly"])
    await ingest_file(
        llm=llm, store=store, db=db, settings=settings, filename="x.pdf", data=data, uploaded_by="a"
    )
    hits = await store.search(hash_embed("rockets fuel"), k=1)
    assert hits[0].chunk.page == 2


async def test_ingest_image_uses_caption(llm, store, db, settings):
    doc = await ingest_file(
        llm=llm, store=store, db=db, settings=settings, filename="menu.png", data=b"\x89PNG", uploaded_by="a"
    )
    assert doc.kind == "image"
    hits = await store.search(hash_embed("pizza menu"), k=1)
    assert hits[0].chunk.kind == "image_caption"


async def test_reupload_replaces_old_chunks(llm, store, db, settings):
    kw = {"llm": llm, "store": store, "db": db, "settings": settings, "uploaded_by": "a"}
    await ingest_file(filename="n.txt", data=b"alpha beta gamma", **kw)
    await ingest_file(filename="n.txt", data=b"delta epsilon", **kw)
    texts = [h.chunk.text for h in await store.search(hash_embed("alpha delta"), k=10)]
    assert texts == ["delta epsilon"]


async def test_empty_and_unsupported_files_rejected(llm, store, db, settings):
    kw = {"llm": llm, "store": store, "db": db, "settings": settings, "uploaded_by": "a"}
    with pytest.raises(UnsupportedFileError):
        await ingest_file(filename="e.txt", data=b"  ", **kw)
    with pytest.raises(UnsupportedFileError):
        await ingest_file(filename="a.exe", data=b"x", **kw)


async def test_dimension_mismatch_is_refused_then_reindex_fixes_it(llm, db, settings):
    store = MemoryVectorStore()
    kw = {"llm": llm, "store": store, "db": db, "settings": settings, "uploaded_by": "a"}
    await ingest_file(filename="n.txt", data=b"alpha beta", **kw)

    llm.embedding_dim = 32  # simulate switching to a different embedding model
    with pytest.raises(ValueError, match="recreate"):
        await ingest_file(filename="m.txt", data=b"gamma", **kw)

    async def small_embed(texts, *, is_query):
        return [hash_embed(t)[:32] for t in texts]

    llm.embed = small_embed  # type: ignore[method-assign]
    written = await reindex_all(llm=llm, store=store, db=db, settings=settings)
    assert written == 1
    assert len(await store.search([1.0] * 32, k=5)) == 1


async def test_db_documents_roundtrip(db):
    await db.add_document(DocumentRow(id="1", name="a", kind="text", chunks=2, uploaded_by="x", ts=""))
    assert (await db.list_documents())[0].chunks == 2
