import re
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shelf_finder.douban import douban_search_url
from shelf_finder.library import Library, PhotoNotFound, SpineNotFound
from shelf_finder.recognition import Box

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
# 手机浏览器的标识里都带这几个词之一，用来决定给豆瓣手机版还是桌面版的搜索页
MOBILE_BROWSER = re.compile(r"Mobi|Android|iPhone|iPad", re.IGNORECASE)


class RecognitionDisabled(Exception):
    pass


def split_tags(text: str) -> list[str] | None:
    # 表单里的标签用顿号、逗号或空格隔开；空字符串表示这次不改标签
    parts = [t for t in re.split(r"[、，,\s]+", text) if t]
    return parts or None


def split_adaptations(text: str) -> list[tuple[str, str]]:
    # 每行一部作品：「标题 链接」，链接可以没有
    items = []
    for line in text.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        url = parts[-1] if parts[-1].startswith("http") and len(parts) > 1 else ""
        title = " ".join(parts[:-1] if url else parts)
        items.append((title, url))
    return items


def douban_links(request: Request, books) -> dict[int, str]:
    # 每本书的豆瓣链接，按书 id 索引：存了条目链接就用它，否则给搜索页；手机浏览器给手机版搜索页
    mobile = bool(MOBILE_BROWSER.search(request.headers.get("user-agent", "")))
    return {book.id: book.douban_url or douban_search_url(book.title, mobile) for book in books}


def create_app(library: Library, recognition: bool = True) -> FastAPI:
    app = FastAPI(title="Shelf Finder")
    # 模板通过它决定要不要显示上传表单和「重新识别」
    TEMPLATES.env.globals["recognition"] = recognition

    def require_recognition():
        if not recognition:
            raise RecognitionDisabled()

    @app.exception_handler(RecognitionDisabled)
    def recognition_disabled(request: Request, exc: RecognitionDisabled):
        return PlainTextResponse("这个部署只查看已有数据，不提供上传和识别", status_code=403)

    @app.exception_handler(PhotoNotFound)
    def photo_not_found(request: Request, exc: PhotoNotFound):
        return PlainTextResponse("没有这张照片", status_code=404)

    @app.exception_handler(SpineNotFound)
    def spine_not_found(request: Request, exc: SpineNotFound):
        return PlainTextResponse("没有这条书脊", status_code=404)

    @app.exception_handler(AssertionError)
    def invalid_input(request: Request, exc: AssertionError):
        # 存储层对表单内容的校验（空文字、表外标签等）以断言表达，对使用者就是输入有误
        return PlainTextResponse(str(exc) or "输入有误", status_code=422)

    @app.get("/")
    def index(request: Request, q: str = ""):
        # q 是从搜索结果页或照片页返回时带回来的上一次关键字，填回搜索框
        counts = library.spine_counts()
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "photos": library.list_photos(),
                "counts": counts,
                "total_books": len(library.list_books()),
                "tag_counts": library.tag_counts(),
                "q": q,
            },
        )

    @app.post("/photos")
    def add_photo(name: str = Form(...), image: UploadFile = File(...)):
        require_recognition()
        photo = library.add_photo(name, image.file.read())
        return RedirectResponse(f"/photos/{photo.id}", status_code=303)

    @app.get("/search")
    def search(request: Request, q: str = ""):
        # 按照片分组，组的顺序跟随组内最好的命中；不在任何照片上的书归到最后一组
        groups = {}
        hits = library.search(q)
        for hit in hits:
            key = hit.photo.id if hit.photo else None
            groups.setdefault(key, (hit.photo, []))[1].append(hit)
        ordered = [groups[k] for k in groups if k is not None] + ([groups[None]] if None in groups else [])
        douban = douban_links(request, [hit.book for hit in hits])
        return TEMPLATES.TemplateResponse(request, "search.html", {"q": q, "groups": ordered, "douban": douban})

    @app.get("/photos/{photo_id}")
    def photo_page(request: Request, photo_id: int, q: str = ""):
        photo = library.get_photo(photo_id)
        spines = library.spines(photo_id)
        matched = {book.id for book, _ in library.search_books(q)}
        hit_ids = {spine_id for spine_id, _, book in spines if book.id in matched}
        return TEMPLATES.TemplateResponse(
            request,
            "photo.html",
            {
                "photo": photo,
                "spines": spines,
                "douban": douban_links(request, [book for _, _, book in spines]),
                "vocabulary": library.vocabulary(),
                "q": q,
                "hit_ids": hit_ids,
                "manual_edits": library.manual_edits(photo_id),
            },
        )

    def box_from_form(x1: int, y1: int, x2: int, y2: int) -> Box:
        box = Box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        assert box.x2 > box.x1 and box.y2 > box.y1, "框不能是空的"
        return box

    @app.post("/photos/{photo_id}/spines/{spine_id}/update")
    def update_spine(
        photo_id: int,
        spine_id: int,
        text: str = Form(...),
        x1: int | None = Form(None),
        y1: int | None = Form(None),
        x2: int | None = Form(None),
        y2: int | None = Form(None),
        tags: str = Form(""),
        douban_url: str = Form(""),
        adaptations: str = Form(""),
    ):
        # 文字和框改在书脊上；标签、豆瓣链接、改编作品改在这条书脊指向的书上。
        # 没重画框时四个坐标都不传，框保持原样；标签留空表示不改
        box = None if x1 is None else box_from_form(x1, y1, x2, y2)
        book = next(book for sid, _, book in library.spines(photo_id) if sid == spine_id)
        library.update_spine(spine_id, text=text, box=box)
        library.update_book(
            book.id, tags=split_tags(tags), douban_url=douban_url, adaptations=split_adaptations(adaptations)
        )
        return RedirectResponse(f"/photos/{photo_id}#spine-{spine_id}", status_code=303)

    @app.post("/photos/{photo_id}/spines/{spine_id}/insert")
    def insert_spine(
        photo_id: int,
        spine_id: int,
        text: str = Form(...),
        x1: int = Form(...),
        y1: int = Form(...),
        x2: int = Form(...),
        y2: int = Form(...),
    ):
        new_id = library.insert_spine_after(spine_id, text, box_from_form(x1, y1, x2, y2))
        return RedirectResponse(f"/photos/{photo_id}#spine-{new_id}", status_code=303)

    @app.post("/photos/{photo_id}/spines/{spine_id}/delete")
    def delete_spine(photo_id: int, spine_id: int):
        # 删完回到相邻的那一行，列表不会跳回最上面
        ids = [i for i, _, _ in library.spines(photo_id)]
        library.delete_spine(spine_id)
        index = ids.index(spine_id)
        below = ids[index + 1 : index + 2]
        above = ids[index - 1 : index] if index else []
        neighbors = below or above
        anchor = f"#spine-{neighbors[0]}" if neighbors else ""
        return RedirectResponse(f"/photos/{photo_id}{anchor}", status_code=303)

    @app.get("/photos/{photo_id}/image")
    def photo_image(photo_id: int):
        library.get_photo(photo_id)
        return FileResponse(library.photo_path(photo_id), media_type="image/jpeg")

    @app.post("/photos/{photo_id}/rename")
    def rename_photo(photo_id: int, name: str = Form(...)):
        if not name.strip():
            return PlainTextResponse("照片名字不能为空", status_code=422)
        library.rename_photo(photo_id, name)
        return RedirectResponse(f"/photos/{photo_id}", status_code=303)

    @app.post("/photos/{photo_id}/recognize")
    def recognize_again(photo_id: int):
        require_recognition()
        library.recognize_again(photo_id)
        return RedirectResponse(f"/photos/{photo_id}", status_code=303)

    @app.post("/photos/{photo_id}/delete")
    def delete_photo(photo_id: int):
        library.delete_photo(photo_id)
        return RedirectResponse("/", status_code=303)

    return app
