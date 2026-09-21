import pathlib

import pytest

import xgen_pdf

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def korean_path() -> pathlib.Path:
    return FIXTURES / "korean.pdf"


@pytest.fixture
def latin_path() -> pathlib.Path:
    return FIXTURES / "latin.pdf"


@pytest.fixture
def encrypted_path() -> pathlib.Path:
    return FIXTURES / "encrypted.pdf"


@pytest.fixture
def korean(korean_path):
    doc = xgen_pdf.open(korean_path)
    yield doc
    doc.close()


@pytest.fixture
def latin(latin_path):
    doc = xgen_pdf.open(latin_path)
    yield doc
    doc.close()
