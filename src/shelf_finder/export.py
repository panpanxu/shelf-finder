import json
import shutil
from pathlib import Path

import opencc
import regex
from jinja2 import Environment, FileSystemLoader, select_autoescape

from shelf_finder.douban import douban_search_url, search_query
from shelf_finder.library import Library, compact

PACKAGE = Path(__file__).parent
HAN = regex.compile(r"\p{Han}")
# 简转繁的几套用字（大陆通行繁体、台湾、香港），合起来覆盖使用者可能输入的繁体字
TO_TRADITIONAL = [opencc.OpenCC(name) for name in ("s2t", "s2tw", "s2hk")]


def export_site(library: Library, out_dir) -> None:
    # 把整个库导出成一组静态页面：首页、每张照片一页、搜索页，加上浏览器端搜索用的数据。
    # 没有服务器，所以不含上传、识别和编辑。
    out = Path(out_dir)
    (out / "photos").mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(PACKAGE / "templates"), autoescape=select_autoescape(["html"]))

    photos = library.list_photos()
    counts = library.spine_counts()
    spines_by_photo = {photo.id: library.spines(photo.id) for photo in photos}

    (out / "index.html").write_text(
        env.get_template("static/index.html").render(
            page="index",
            root="",
            photos=photos,
            counts=counts,
            total_books=sum(counts.values()),
            tag_counts=library.tag_counts(),
        )
    )
    (out / "search.html").write_text(env.get_template("static/search.html").render(page="search", root=""))

    for photo in photos:
        spines = spines_by_photo[photo.id]
        shutil.copyfile(library.photo_path(photo.id), out / "photos" / f"{photo.id}.jpg")
        (out / "photos" / f"{photo.id}.html").write_text(
            env.get_template("static/photo.html").render(
                page="photo",
                root="../",
                photo=photo,
                spines=spines,
                douban={book.id: douban_search_url(book.title) for _, _, book in spines},
                douban_query={book.id: search_query(book.title) for _, _, book in spines},
            )
        )

    # 浏览器端按书搜索；每本书只保留它所在最新照片上的那条书脊作为位置
    data = {
        "photos": [{"id": p.id, "name": p.name} for p in photos],
        "vocabulary": library.vocabulary(),
        "spines": [
            {
                "id": spine_id,
                "photo": photo.id,
                "order": order,
                "book": book.id,
                "text": book.title,
                "key": compact(book.title),
                "tags": list(book.tags),
                "box": [spine.box.x1, spine.box.y1, spine.box.x2, spine.box.y2],
                "dq": search_query(book.title),
                "douban": book.douban_url,
                "adaptations": [list(a) for a in book.adaptations],
            }
            for photo in photos
            for order, (spine_id, spine, book) in enumerate(spines_by_photo[photo.id])
        ],
    }
    data["t2s"] = traditional_to_simplified_map("".join(s["key"] for s in data["spines"]) + "".join(data["vocabulary"]))
    (out / "data.js").write_text("window.SHELF_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    shutil.copyfile(PACKAGE / "static" / "site.js", out / "site.js")
    # 告诉 GitHub Pages 原样发布，不要用 Jekyll 处理
    (out / ".nojekyll").write_text("")


def traditional_to_simplified_map(simplified_text: str) -> dict[str, str]:
    # 只收录库里出现过的汉字的繁体写法，够用且很小
    mapping = {}
    for char in sorted(set(HAN.findall(simplified_text))):
        for converter in TO_TRADITIONAL:
            traditional = converter.convert(char)
            if traditional != char and traditional not in mapping:
                mapping[traditional] = char
    return mapping
