import pytest
from conftest import compact

from shelf_finder.library import Library
from shelf_finder.recognition import Box


@pytest.fixture
def lib(tmp_path, shelf):
    lib = Library(tmp_path)
    lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    return lib


def texts(lib, photo_id):
    return [compact(spine.text) for _, spine, _ in lib.spines(photo_id)]


def test_edited_spine_text_is_what_search_finds(lib):
    spine_id, spine = next((i, s) for i, s, _ in lib.spines(1) if "算法导论" in s.text)

    lib.update_spine(spine_id, text="算法导论（原书第3版） 机械工业出版社")

    assert lib.search("第3版")[0].spine_id == spine_id
    assert lib.search("第3版")[0].spine.box == spine.box
    assert lib.manual_edits(1) == 1


def test_inserted_spine_sits_right_below_the_chosen_row(lib):
    before = texts(lib, 1)
    anchor_id = lib.spines(1)[1][0]
    box = Box(300, 80, 360, 820)

    new_id = lib.insert_spine_after(anchor_id, "活着 余华", box)

    assert texts(lib, 1) == before[:2] + ["活着余华"] + before[2:]
    assert lib.search("活着")[0].spine_id == new_id
    assert lib.search("活着")[0].spine.box == box
    assert lib.manual_edits(1) == 1


def test_deleted_spine_vanishes_from_list_and_search(lib):
    before = texts(lib, 1)
    victim_id = next(i for i, s, _ in lib.spines(1) if "红楼梦" in s.text)

    lib.delete_spine(victim_id)

    assert texts(lib, 1) == [t for t in before if "红楼梦" not in t]
    assert lib.search("红楼梦") == []
