import pytest

from xgen_pdf import Rect


def test_find_tables_on_ruled_grid(korean):
    finder = korean[0].find_tables()
    assert len(finder) == 1 and len(finder.tables) == 1
    table = finder[0]
    assert (table.row_count, table.col_count) == (3, 3)
    assert table.extract() == [["항목", "값", "비고"], ["속도", "빠름", "기본"], ["정확도", "높음", "검증됨"]]
    x0, y0, x1, y1 = table.bbox
    assert abs(x0 - 40) < 2 and abs(y0 - 300) < 2 and abs(x1 - 460) < 2 and abs(y1 - 366) < 2
    assert len(table.cells) == 9 and all(cell is not None for cell in table.cells)
    assert len(table.rows) == 3 and len(table.rows[0].cells) == 3
    assert table.header.names == ["항목", "값", "비고"]
    assert table.header.external is False
    assert len(finder.cells) == 9


def test_table_markdown(korean):
    table = korean[0].find_tables()[0]
    md = table.to_markdown()
    lines = md.strip().splitlines()
    assert lines[0] == "|항목|값|비고|"
    assert lines[1] == "|---|---|---|"
    assert lines[2] == "|속도|빠름|기본|"
    assert lines[3] == "|정확도|높음|검증됨|"


def test_table_pandas(korean):
    pd = pytest.importorskip("pandas")
    frame = korean[0].find_tables()[0].to_pandas()
    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == ["항목", "값", "비고"]
    assert frame.shape == (2, 3)


def test_tolerance_settings_are_accepted(korean):
    finder = korean[0].find_tables(snap_tolerance=7, join_tolerance=7, edge_min_length=10, intersection_tolerance=7)
    assert len(finder.tables) == 1
    with pytest.raises(TypeError):
        korean[0].find_tables(bogus=1)


def test_clip_restricts_search(korean):
    assert len(korean[0].find_tables(clip=Rect(0, 0, 500, 280)).tables) == 0
    assert len(korean[0].find_tables(clip=Rect(0, 280, 500, 400)).tables) == 1


def test_text_strategy_finds_aligned_columns(korean):
    finder = korean[0].find_tables(strategy="text", clip=Rect(30, 290, 470, 380))
    assert finder.tables
    assert finder.tables[0].row_count >= 3


def test_no_tables_on_plain_page(latin):
    assert latin[1].find_tables().tables == []
