from pathlib import Path

import pytest
from conftest import compact

from shelf_finder.library import normalize_photo
from shelf_finder.recognition import recognize

REAL_SHELF = Path(__file__).parent / "fixtures" / "real_shelf.heic"


@pytest.fixture(scope="module")
def real_spines():
    return recognize(normalize_photo(REAL_SHELF.read_bytes()))


def spines_with(spines, *words):
    return [s for s in spines if all(compact(w) in compact(s.text) for w in words)]


def test_each_book_on_the_shelf_becomes_one_spine_at_its_position(shelf):
    image, books = shelf("shelf_a")

    spines = recognize(image)

    assert len(spines) == len(books)
    for book in books:
        on_book = [s for s in spines if book["x1"] <= (s.box.x1 + s.box.x2) / 2 <= book["x2"]]
        assert len(on_book) == 1, book["title"]
        spine = on_book[0]
        assert compact(book["title"]) in compact(spine.text)
        assert compact(book["sub"]) in compact(spine.text)
        assert book["x1"] - 10 <= spine.box.x1 and spine.box.x2 <= book["x2"] + 10
        assert book["y1"] - 10 <= spine.box.y1 and spine.box.y2 <= book["y2"] + 10


def test_books_of_one_series_on_a_real_shelf_stay_separate(real_spines):
    for title in ["函数和图像", "三角函数", "坐标方法"]:
        assert len(spines_with(real_spines, title)) == 1, title
    assert spines_with(real_spines, "函数和图像") != spines_with(real_spines, "三角函数")
    assert spines_with(real_spines, "三角函数") != spines_with(real_spines, "坐标方法")
    # 照片上并排放着两本《初等数论》
    assert len(spines_with(real_spines, "初等数论")) == 2


def test_columns_of_one_wide_spine_on_a_real_shelf_merge(real_spines):
    assert len(spines_with(real_spines, "人类仰望星空时", "COSMOS")) == 1
    assert len(spines_with(real_spines, "费马大定理", "困惑")) == 1
    assert len(spines_with(real_spines, "THIRTEEN", "欧几里得")) == 1


def test_publisher_lines_on_a_real_shelf_join_their_books(real_spines):
    for spine in real_spines:
        # 每条书脊上最多只有一家出版社，出版社行不会自己凑成一本书
        assert compact(spine.text).count("出版社") <= 1, spine.text
    assert len(spines_with(real_spines, "更高更妙的高中数学思想与方法", "浙江大学出版社")) == 1
