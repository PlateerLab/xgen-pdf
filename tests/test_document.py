import io

import pytest

import xgen_pdf as xp


def test_open_forms_and_page_access(latin_path):
    data = latin_path.read_bytes()
    for doc in (
        xp.open(latin_path),
        xp.open(str(latin_path)),
        xp.open(stream=data, filetype="pdf"),
        xp.Document(stream=io.BytesIO(data)),
    ):
        assert len(doc) == 2 and doc.page_count == 2
        assert doc[0].number == 0 and doc[-1].number == 1
        assert [p.number for p in doc] == [0, 1]
        assert doc[0] is doc.load_page(0)
        with pytest.raises(IndexError):
            doc[5]
        doc.close()


def test_context_manager_and_closed_state(latin_path):
    with xp.open(latin_path) as doc:
        assert doc.page_count == 2
    assert doc.is_closed


def test_page_geometry(latin):
    page = latin[0]
    assert tuple(page.rect) == (0, 0, 400, 300)
    assert page.bound() == page.rect
    assert page.rotation == 0
    assert tuple(page.mediabox) == (0, 0, 400, 300)
    assert page.parent is latin
    assert tuple(latin[1].rect) == (0, 0, 200, 200)


def test_metadata_keys(latin):
    meta = latin.metadata
    for key in (
        "format",
        "title",
        "author",
        "subject",
        "keywords",
        "creator",
        "producer",
        "creationDate",
        "modDate",
        "encryption",
    ):
        assert key in meta
    assert meta["format"].startswith("PDF")


def test_broken_input_raises_file_data_error():
    with pytest.raises(xp.FileDataError):
        xp.open(stream=b"not a pdf at all", filetype="pdf")


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        xp.open("/nonexistent/file.pdf")


def test_encrypted_document_needs_password(encrypted_path):
    doc = xp.open(encrypted_path)
    assert doc.needs_pass and doc.is_encrypted
    assert doc.page_count == 0
    assert doc.authenticate("wrong") == 0
    assert doc.authenticate("secret") != 0
    assert not doc.needs_pass
    assert "Secret page" in doc[0].get_text("text")


def test_tobytes_round_trip(latin):
    data = latin.tobytes()
    assert data.startswith(b"%PDF")
    again = xp.open(stream=data, filetype="pdf")
    assert again.page_count == 2
    assert again[0].get_text("text") == latin[0].get_text("text")


def test_save_writes_file(latin, tmp_path):
    out = tmp_path / "copy.pdf"
    latin.save(out)
    assert xp.open(out).page_count == 2
