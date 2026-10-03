import pytest
from conftest import compact

from shelf_finder.library import Library


@pytest.fixture(scope="module")
def library(tmp_path_factory, request):
    # 识别要调用两个模型服务，整个模块共用一份入库结果
    fixtures = request.config.rootpath / "tests" / "fixtures"
    lib = Library(tmp_path_factory.mktemp("data"))
    lib.add_photo("书房左架", (fixtures / "shelf_a.jpg").read_bytes())
    lib.add_photo("客厅矮柜", (fixtures / "shelf_b.jpg").read_bytes())
    return lib


def test_keyword_finds_the_spine_and_names_the_photo(library, shelf):
    _, books = shelf("shelf_a")
    target = next(b for b in books if b["title"] == "统计学习方法")

    hits = library.search("统计学习")

    assert len(hits) == 1
    hit = hits[0]
    assert hit.photo.name == "书房左架"
    assert "统计学习方法" in compact(hit.spine.text)
    assert target["x1"] <= (hit.spine.box.x1 + hit.spine.box.x2) / 2 <= target["x2"]


def test_keyword_on_several_shelves_hits_every_photo(library):
    hits = library.search("机械工业出版社")

    assert sorted((h.photo.name, compact(h.spine.text)) for h in hits) == [
        ("书房左架", compact("深入理解计算机系统 机械工业出版社")),
        ("客厅矮柜", compact("算法导论 机械工业出版社")),
    ]


def test_keyword_tolerates_one_wrong_character_but_not_two(library):
    one_wrong = library.search("深入理解计箅机系统")
    assert [compact(h.spine.text) for h in one_wrong] == [compact("深入理解计算机系统 机械工业出版社")]
    assert one_wrong[0].errors == 1

    # 「人民文学出版社」与「人民邮电出版社」差两个字
    assert library.search("人民邮电出版社") == []


def test_simplified_and_traditional_chinese_match_each_other(tmp_path, shelf):
    lib = Library(tmp_path)
    lib.add_photo("书房左架", shelf("shelf_a")[0])
    santi_id = next(i for i, s in lib.spines(1) if "三体" in s.text)
    lib.update_spine(santi_id, text="三體全集 劉慈欣")

    # 繁体关键字找简体书脊
    assert [compact(h.spine.text) for h in lib.search("統計學習方法")] == [compact("统计学习方法 李航著")]
    # 简体关键字找繁体书脊
    assert [h.spine_id for h in lib.search("刘慈欣")] == [santi_id]
    # 关键字里混着一个繁体字
    assert [compact(h.spine.text) for h in lib.search("深入理解計算机系统")] == [compact("深入理解计算机系统 机械工业出版社")]
    assert lib.search("統計學習方法")[0].errors == 0


def test_deleted_photo_leaves_no_hits(tmp_path, shelf):
    lib = Library(tmp_path)
    kept = lib.add_photo("书房左架", shelf("shelf_a")[0])
    gone = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])

    lib.delete_photo(gone.id)

    assert lib.search("算法导论") == []
    assert not lib.photo_path(gone.id).exists()
    assert [p.name for p in lib.list_photos()] == ["书房左架"]
    assert lib.search("统计学习方法")[0].photo == kept


def test_recognizing_again_replaces_the_spines(tmp_path, shelf):
    lib = Library(tmp_path)
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    before = lib.spines(photo.id)

    lib.recognize_again(photo.id)

    after = lib.spines(photo.id)
    assert len(after) == len(before) == 4
    assert len(lib.search("百年孤独")) == 1
