from urllib.parse import parse_qs, urlparse

from shelf_finder.douban import douban_search_url


def search_text(url):
    return parse_qs(urlparse(url).query)["search_text"][0]


def test_search_uses_title_author_and_publisher_but_drops_english_title_edition_and_nationality():
    url = douban_search_url("数论概论 Number Theory: A Friendly Introduction（原书第4版）[美]约瑟夫·H·西尔弗曼 机械工业出版社 China Machine Press")

    assert url.startswith("https://search.douban.com/book/subject_search?")
    assert search_text(url) == "数论概论 约瑟夫·H·西尔弗曼 机械工业出版社"


def test_search_strips_edition_and_role_words_and_converts_to_simplified():
    assert search_text(douban_search_url("更高更妙的高中数学思想与方法 第十四版 蔡小雄著 蔡天乐审校 浙江大学出版社")) == (
        "更高更妙的高中数学思想与方法 蔡小雄 蔡天乐 浙江大学出版社"
    )
    assert search_text(douban_search_url("马同学图解线性代数 電子工業出版社")) == "马同学图解线性代数 电子工业出版社"
    assert search_text(douban_search_url("普林斯顿微积分读本（修订版） Adrian Banner 杨爽 赵晓婷 高璞 译 人民邮电出版社")) == (
        "普林斯顿微积分读本 杨爽 赵晓婷 高璞 人民邮电出版社"
    )


def test_english_title_keeps_its_whole_text_and_chinese_parts_follow():
    assert search_text(douban_search_url("The Cartoon Guide to Calculus")) == "The Cartoon Guide to Calculus"
    assert search_text(douban_search_url("Number Theory: Concepts and Problems [保]奥列格·马史卡洛夫")) == (
        "Number Theory: Concepts and Problems 奥列格·马史卡洛夫"
    )
    assert search_text(douban_search_url("COSMOS HUMAN THE 人类仰望星空时")) == "COSMOS HUMAN THE 人类仰望星空时"


def test_spine_starting_with_nationality_still_searches_its_chinese_parts():
    assert search_text(douban_search_url("[美]杰森·威尔克斯 烧掉数学书")) == "杰森·威尔克斯 烧掉数学书"


def test_phone_gets_the_mobile_search_page():
    url = douban_search_url("百年孤独 南海出版公司", mobile=True)

    assert url.startswith("https://m.douban.com/search/?")
    assert parse_qs(urlparse(url).query) == {"query": ["百年孤独 南海出版公司"], "type": ["book"]}
