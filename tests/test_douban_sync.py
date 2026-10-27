import pytest

from shelf_finder.douban_sync import find_adaptations, find_subject
from shelf_finder.library import Library

# 这些测试通过 douban-cli 真实访问豆瓣，结果随豆瓣数据变化，断言只盯稳定的事实


def test_subject_is_the_best_rated_edition_from_the_same_publisher():
    url = find_subject("数论概论 Number Theory: A Friendly Introduction（原书第4版）[美]约瑟夫·H·西尔弗曼 机械工业出版社")
    assert url == "https://book.douban.com/subject/3099540/"


def test_no_subject_when_douban_only_has_other_books_from_that_publisher():
    # 豆瓣搜「从故事到理论」返回的是同一出版社的《数论入门》，书名对不上就不能算
    assert find_subject("从故事到理论 单墫 中国科学技术大学出版社") is None


def test_english_title_matches_the_publisher_loosely():
    url = find_subject("Designing Data-Intensive Applications O'Reilly")
    assert url is not None and url.startswith("https://book.douban.com/subject/")


def test_adaptation_needs_a_film_whose_summary_says_it_was_adapted_from_the_author():
    santi = find_adaptations("三体全集 刘慈欣")
    assert any(url == "https://movie.douban.com/subject/26647087/" for _, url in santi)
    assert all("三体" in title for title, _ in santi)
    assert find_adaptations("算法导论 机械工业出版社") == []


def test_sync_fills_books_once_and_skips_them_afterwards(tmp_path, shelf):
    lib = Library(tmp_path)
    photo = lib.add_photo("客厅矮柜", shelf("shelf_b")[0])

    first = lib.sync_douban(delay=0)

    books = {book.title: book for _, _, book in lib.spines(photo.id)}
    assert books["百年孤独 南海出版公司"].douban_url.startswith("https://book.douban.com/subject/")
    assert any("百年孤独" in title for title, _ in books["百年孤独 南海出版公司"].adaptations)
    assert books["算法导论 机械工业出版社"].adaptations == ()
    assert first["looked_up"] == 4 and first["subjects"] >= 3

    second = lib.sync_douban(delay=0)
    assert second["looked_up"] == 0
