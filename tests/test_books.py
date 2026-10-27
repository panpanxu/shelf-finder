import pytest
from conftest import compact

from shelf_finder.library import Library
from shelf_finder.recognition import Box

VOCABULARY = ["小说", "计算机", "数学", "历史", "其他"]


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path)
    lib.set_vocabulary(VOCABULARY)
    return lib


def by_title(lib, photo_id):
    return {compact(spine.text): (spine_id, book) for spine_id, spine, book in lib.spines(photo_id)}


def test_every_spine_points_to_a_book_and_the_same_title_on_another_photo_claims_it(lib, shelf):
    a = lib.add_photo("书房左架", shelf("shelf_a")[0])
    b = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    assert len(lib.list_books()) == 9
    books_a = by_title(lib, a.id)
    santi_id, santi = books_a[compact("三体全集 刘慈欣")]
    assert santi.title == "三体全集 刘慈欣" and "小说" in santi.tags

    # 同一面书架搬去别处再拍一张，书脊认领原来的书，不新建
    a2 = lib.add_photo("新书房", shelf("shelf_a")[0])
    assert len(lib.list_books()) == 9
    assert by_title(lib, a2.id)[compact("三体全集 刘慈欣")][1].id == santi.id

    # 同一张照片上两条一样的书脊是两本书
    new_spine = lib.insert_spine_after(santi_id, "三体全集 刘慈欣", Box(10, 10, 60, 500))
    books = {sid: book for sid, _, book in lib.spines(a.id)}
    assert books[new_spine].id != santi.id
    assert len(lib.list_books()) == 10


def test_book_keeps_its_douban_link_and_adaptations_when_the_photo_is_recognized_again(lib, shelf):
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    _, book = by_title(lib, photo.id)[compact("百年孤独 南海出版公司")]
    lib.update_book(
        book.id,
        douban_url="https://book.douban.com/subject/6082808/",
        adaptations=[("百年孤独（剧集）", "https://movie.douban.com/subject/30267587/")],
    )

    lib.recognize_again(photo.id)

    _, again = by_title(lib, photo.id)[compact("百年孤独 南海出版公司")]
    assert again.id == book.id
    assert again.douban_url == "https://book.douban.com/subject/6082808/"
    assert again.adaptations == (("百年孤独（剧集）", "https://movie.douban.com/subject/30267587/"),)
    assert len(lib.list_books()) == 4


def test_search_finds_the_book_at_its_latest_photo_and_deleting_photos_removes_unplaced_books(lib, shelf):
    old = lib.add_photo("旧书房", shelf("shelf_a")[0])
    new = lib.add_photo("新书房", shelf("shelf_a")[0])

    hits = lib.search("统计学习方法")
    assert len(hits) == 1 and hits[0].photo.id == new.id and hits[0].book.title.startswith("统计学习方法")

    lib.delete_photo(new.id)
    assert lib.search("统计学习方法")[0].photo.id == old.id

    lib.delete_photo(old.id)
    assert lib.search("统计学习方法") == []
    assert lib.list_books() == []


def test_books_the_new_recognition_missed_stay_without_a_location(lib, shelf):
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    spine_id, book = by_title(lib, photo.id)[compact("人类简史 中信出版社")]
    lib.update_book(book.id, douban_url="https://book.douban.com/subject/25985021/")
    # 模拟识别没读出这本书：只删它的书脊，不走会连带删书的删除操作
    with lib._connect() as db:
        db.execute("DELETE FROM spines WHERE id = ?", (spine_id,))

    hits = lib.search("人类简史")
    assert len(hits) == 1 and hits[0].photo is None and hits[0].spine is None
    assert hits[0].book.douban_url == "https://book.douban.com/subject/25985021/"


def test_correcting_the_spine_text_renames_its_book(lib, shelf):
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])
    spine_id, book = by_title(lib, photo.id)[compact("算法导论 机械工业出版社")]

    lib.update_spine(spine_id, text="算法导论（原书第3版） 机械工业出版社")

    assert lib.get_book(book.id).title == "算法导论（原书第3版） 机械工业出版社"
    assert lib.search("第3版")[0].book.id == book.id
