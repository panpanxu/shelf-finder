import json
import re

import pytest

from shelf_finder.export import export_site
from shelf_finder.library import Library

VOCABULARY = ["小说", "计算机", "数学", "历史", "其他"]


@pytest.fixture(scope="module")
def site(tmp_path_factory, request):
    fixtures = request.config.rootpath / "tests" / "fixtures"
    lib = Library(tmp_path_factory.mktemp("data"))
    lib.set_vocabulary(VOCABULARY)
    lib.add_photo("书房左架", (fixtures / "shelf_a.jpg").read_bytes())
    lib.add_photo("客厅矮柜", (fixtures / "shelf_b.jpg").read_bytes())
    out = tmp_path_factory.mktemp("site")
    export_site(lib, out)
    return lib, out


def test_static_site_has_a_page_per_photo_with_its_image_and_boxes(site):
    lib, out = site
    index = (out / "index.html").read_text()
    assert "书房左架" in index and "客厅矮柜" in index and "9 本书" in index
    assert 'href="photos/1.html"' in index

    page = (out / "photos" / "1.html").read_text()
    assert (out / "photos" / "1.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert 'src="1.jpg"' in page
    assert len(re.findall(r"<rect data-id=", page)) == 5
    assert "统计学习方法" in page and "search.douban.com" in page
    # 静态站没有服务器，不提供编辑、上传和重新识别
    assert "重新识别" not in page and 'data-act="edit"' not in page and 'action="/photos"' not in index


def test_static_site_ships_the_search_data_and_a_search_page(site):
    lib, out = site
    data = json.loads((out / "data.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    assert len(data["spines"]) == 9
    spine = next(s for s in data["spines"] if "统计学习方法" in s["text"])
    assert spine["photo"] == 1 and spine["box"] == [spine["box"][0], spine["box"][1], spine["box"][2], spine["box"][3]]
    assert set(spine["tags"]) <= set(VOCABULARY)
    assert "统计学习方法" in spine["key"]  # 去空白、转简体后的匹配用文本
    assert data["vocabulary"] == VOCABULARY
    # 繁体关键字要能搜到简体书脊，数据里带一张繁转简的字表
    assert data["t2s"]["統"] == "统"
    assert (out / "search.html").exists() and (out / "site.js").exists()
