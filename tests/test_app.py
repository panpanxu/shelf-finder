import re

import pytest
from fastapi.testclient import TestClient

from shelf_finder.app import create_app
from shelf_finder.library import Library


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Library(tmp_path)))


def upload(client, name, image):
    return client.post("/photos", data={"name": name}, files={"image": ("shelf.jpg", image, "image/jpeg")})


def test_uploaded_photo_page_shows_its_name_spines_and_image(client, shelf):
    image, _ = shelf("shelf_a")

    response = upload(client, "书房左架", image)

    assert response.status_code == 200
    assert "书房左架" in response.text
    assert "统计学习方法" in response.text
    photo_url = str(response.url)
    picture = client.get(photo_url + "/image")
    assert picture.headers["content-type"] == "image/jpeg"
    assert picture.content[:2] == b"\xff\xd8"


def test_search_lists_hits_by_photo_and_highlights_them_on_the_photo(client, shelf):
    upload(client, "书房左架", shelf("shelf_a")[0])
    upload(client, "客厅矮柜", shelf("shelf_b")[0])

    results = client.get("/search", params={"q": "机械工业出版社"})

    assert "书房左架" in results.text and "客厅矮柜" in results.text
    assert "深入理解计算机系统" in results.text and "算法导论" in results.text
    assert "统计学习方法" not in results.text

    photo_page = client.get("/photos/1", params={"q": "统计学习"})
    assert photo_page.text.count('class="hit"') == 2  # 照片上的框和右侧列表各一处
    assert "命中 1 本书" in photo_page.text


def test_spines_can_be_edited_inserted_and_deleted_from_the_photo_page(client, shelf):
    page = upload(client, "客厅矮柜", shelf("shelf_b")[0])
    ids = [int(i) for i in re.findall(r'id="spine-(\d+)"', page.text)]
    assert len(ids) == 4
    first, second = ids[0], ids[1]

    edited = client.post(f"/photos/1/spines/{first}/update", data={"text": "百年孤独（新译本） 南海出版公司"})
    assert edited.status_code == 200 and "百年孤独（新译本）" in edited.text
    assert "手工改过 1 行" in edited.text

    inserted = client.post(
        f"/photos/1/spines/{second}/insert",
        data={"text": "活着 余华", "x1": 300, "y1": 80, "x2": 360, "y2": 820},
    )
    order = [t for t in re.findall(r'id="spine-\d+"[^>]*>\s*<span class="text">([^<]*)</span>', inserted.text)]
    assert order.index("活着 余华") == 2
    assert '<rect data-id="' in inserted.text and 'x="300" y="80"' in inserted.text

    deleted = client.post(f"/photos/1/spines/{first}/delete")
    assert "百年孤独" not in deleted.text
    # 删掉一行后停在它相邻的那一行上，列表不跳回最上面
    assert deleted.history[0].headers["location"] == f"/photos/1#spine-{second}"
    assert client.post("/photos/1/spines/999/delete").status_code == 404


def test_every_spine_links_to_a_douban_search_for_its_title(client, shelf):
    upload(client, "客厅矮柜", shelf("shelf_b")[0])

    desktop = client.get("/photos/1", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}).text
    phone = client.get("/photos/1", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}).text

    desktop_links = re.findall(r'href="(https://search\.douban\.com/[^"]+)"', desktop)
    assert len(desktop_links) == 4
    assert any("search_text=%E7%99%BE%E5%B9%B4%E5%AD%A4%E7%8B%AC" in link for link in desktop_links)  # 百年孤独
    phone_links = re.findall(r'href="(https://m\.douban\.com/search/[^"]+)"', phone)
    assert len(phone_links) == 4
    assert any("query=%E7%99%BE%E5%B9%B4%E5%AD%A4%E7%8B%AC" in link for link in phone_links)

    results = client.get("/search", params={"q": "红楼梦"}).text
    assert len(re.findall(r'href="https://search\.douban\.com/[^"]*search_text=%E7%BA%A2%E6%A5%BC%E6%A2%A6', results)) == 1


def test_going_back_home_from_search_or_photo_keeps_the_keyword(client, shelf):
    upload(client, "客厅矮柜", shelf("shelf_b")[0])

    results = client.get("/search", params={"q": "算法 导论"}).text
    assert 'href="/?q=%E7%AE%97%E6%B3%95%20%E5%AF%BC%E8%AE%BA"' in results
    # 从搜索结果点进照片页，退回去的是搜索结果而不是首页
    photo_page = client.get("/photos/1", params={"q": "算法 导论"}).text
    assert 'href="/search?q=%E7%AE%97%E6%B3%95%20%E5%AF%BC%E8%AE%BA"' in photo_page
    assert 'href="/"' in client.get("/photos/1").text

    home = client.get("/", params={"q": "算法 导论"}).text
    assert 'name="q" value="算法 导论"' in home
    assert 'href="/"' in home  # 首页自己的返回链接不带关键字


VOCABULARY = ["小说", "计算机", "数学", "历史", "其他"]


def test_tags_show_on_the_photo_page_and_can_be_edited_with_the_text(tmp_path, shelf):
    library = Library(tmp_path)
    library.set_vocabulary(VOCABULARY)
    client = TestClient(create_app(library))
    page = upload(client, "客厅矮柜", shelf("shelf_b")[0])
    ids = [int(i) for i in re.findall(r'id="spine-(\d+)"', page.text)]
    honglou = next(i for i, s in library.spines(1) if "红楼梦" in s.text)

    tag_link = '<a class="tag" href="/search?q=%E5%B0%8F%E8%AF%B4">小说</a>'
    assert re.search(rf'id="spine-{honglou}".*?{re.escape(tag_link)}', page.text, re.DOTALL)
    assert 'list="vocabulary"' in page.text and "<option>计算机</option>" in page.text

    edited = client.post(f"/photos/1/spines/{honglou}/update", data={"text": "红楼梦 人民文学出版社", "tags": "历史、小说"})
    assert re.search(rf'id="spine-{honglou}".*?>历史</a>\s*<a class="tag"[^>]*>小说</a>', edited.text, re.DOTALL)

    rejected = client.post(f"/photos/1/spines/{honglou}/update", data={"text": "红楼梦", "tags": "武侠"})
    assert rejected.status_code == 422 and "武侠" in rejected.text

    # 搜标签名得到全部同类的书，结果里也带着可点的标签
    results = client.get("/search", params={"q": "小说"}).text
    assert "百年孤独" in results and "红楼梦" in results and "算法导论" not in results
    assert results.count(tag_link) >= 2

    # 首页列出标签表和每个标签下的书数，点标签就是搜它
    home = client.get("/").text
    assert re.search(r'<a class="tag" href="/search\?q=%E5%B0%8F%E8%AF%B4">小说 <small>\d+</small></a>', home)
    assert "武侠" not in home


def test_home_page_shows_book_totals(client, shelf):
    assert "还没有照片" in client.get("/").text
    upload(client, "书房左架", shelf("shelf_a")[0])
    upload(client, "客厅矮柜", shelf("shelf_b")[0])

    home = client.get("/").text

    assert "2 张照片" in home and "9 本书" in home
    assert "客厅矮柜</a>" in home and "4 本" in home


def test_photo_can_be_renamed_from_its_page(client, shelf):
    page = upload(client, "客厅矮柜", shelf("shelf_b")[0])
    assert 'name="name"' in page.text and "改名" in page.text

    renamed = client.post("/photos/1/rename", data={"name": "客厅书柜第二层"})

    assert renamed.status_code == 200
    assert re.search(r"<h1[^>]*>客厅书柜第二层</h1>", renamed.text)
    assert "客厅书柜第二层" in client.get("/").text
    assert "客厅书柜第二层" in client.get("/search", params={"q": "百年孤独"}).text
    assert client.post("/photos/1/rename", data={"name": "   "}).status_code == 422


def test_viewer_mode_serves_copied_data_without_upload_or_recognition(tmp_path, shelf):
    # 在开发机上识别好的数据目录，原样拷到只做查看的部署上
    Library(tmp_path).add_photo("客厅矮柜", shelf("shelf_b")[0])
    viewer = TestClient(create_app(Library(tmp_path), recognition=False))

    home = viewer.get("/")
    assert "客厅矮柜" in home.text
    assert 'action="/photos"' not in home.text
    assert "算法导论" in viewer.get("/search", params={"q": "算法导论"}).text
    photo_page = viewer.get("/photos/1")
    assert "重新识别" not in photo_page.text and "改名" in photo_page.text

    assert viewer.post("/photos", data={"name": "x"}, files={"image": ("a.jpg", b"", "image/jpeg")}).status_code == 403
    assert viewer.post("/photos/1/recognize").status_code == 403
    assert "算法导论" in viewer.post("/photos/1/rename", data={"name": "客厅书柜"}).text


def test_missing_photo_is_not_found(client):
    assert client.get("/photos/42").status_code == 404
    assert client.get("/photos/42/image").status_code == 404


def test_deleted_photo_disappears_from_the_list_and_search(client, shelf):
    photo_url = str(upload(client, "客厅矮柜", shelf("shelf_b")[0]).url)

    response = client.post(photo_url + "/delete")

    assert response.status_code == 200
    assert "客厅矮柜" not in response.text
    assert "没有找到" in client.get("/search", params={"q": "百年孤独"}).text
