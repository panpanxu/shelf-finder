import pytest
from conftest import compact

from shelf_finder.library import Library

VOCABULARY = ["小说", "计算机", "数学", "数论", "微积分", "历史", "其他"]


@pytest.fixture
def lib(tmp_path, shelf):
    lib = Library(tmp_path)
    lib.set_vocabulary(VOCABULARY)
    return lib


def tags_by_text(lib, photo_id):
    return {compact(spine.text): list(book.tags) for _, spine, book in lib.spines(photo_id)}


def test_vocabulary_is_stored_in_order_and_ends_with_other(lib):
    assert lib.vocabulary() == VOCABULARY


def test_new_photo_books_are_tagged_from_the_vocabulary(lib, shelf):
    photo = lib.add_photo("书房左架", shelf("shelf_a")[0])

    tags = tags_by_text(lib, photo.id)
    assert all(1 <= len(t) <= 3 and set(t) <= set(VOCABULARY) for t in tags.values())
    assert "小说" in tags[compact("三体全集 刘慈欣")]
    assert "计算机" in tags[compact("深入理解计算机系统 机械工业出版社")]


def test_tags_can_be_edited_by_hand_and_survive_recognizing_again(lib, shelf):
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    _, _, book = next(row for row in lib.spines(photo.id) if "红楼梦" in row[1].text)

    lib.update_book(book.id, tags=["历史", "小说"])
    assert lib.get_book(book.id).tags == ("历史", "小说")

    # 重新识别只换书脊，书和它的标签不动
    lib.recognize_again(photo.id)
    assert tags_by_text(lib, photo.id)[compact("红楼梦 人民文学出版社")] == ["历史", "小说"]
    assert lib.manual_edits(photo.id) == 0


def test_retagging_a_photo_applies_the_current_vocabulary_to_every_book(tmp_path, shelf):
    lib = Library(tmp_path)
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    assert all(book.tags == () for _, _, book in lib.spines(photo.id))

    lib.set_vocabulary(VOCABULARY)
    lib.retag_photo(photo.id)

    tags = tags_by_text(lib, photo.id)
    assert all(1 <= len(t) <= 3 and set(t) <= set(VOCABULARY) for t in tags.values())
    assert "小说" in tags[compact("百年孤独 南海出版公司")]


def test_searching_a_tag_name_finds_every_book_with_that_tag(lib, shelf):
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    novels = {book.id for _, _, book in lib.spines(photo.id) if "小说" in book.tags}
    assert len(novels) >= 2  # 百年孤独、红楼梦

    hits = lib.search("小说")

    assert {h.book.id for h in hits} == novels
    assert all(h.errors == 0 for h in hits)
    # 书名里并没有「小说」两个字，是靠标签找到的
    assert all("小说" not in h.book.title for h in hits)


def test_tags_outside_the_vocabulary_are_rejected(lib, shelf):
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    book = lib.spines(photo.id)[0][2]

    with pytest.raises(AssertionError):
        lib.update_book(book.id, tags=["武侠"])
