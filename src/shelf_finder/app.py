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


def douban_links(request: Request, spines) -> dict[int, str]:
    # 每条书脊对应的豆瓣搜索链接，按书脊 id 索引；手机浏览器给手机版搜索页
    mobile = bool(MOBILE_BROWSER.search(request.headers.get("user-agent", "")))
    return {spine_id: douban_search_url(spine.text, mobile) for spine_id, spine in spines}


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
                "total_books": sum(counts.values()),
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
        # 按照片分组，组的顺序跟随组内最好的命中
        groups = {}
        hits = library.search(q)
        for hit in hits:
            groups.setdefault(hit.photo.id, (hit.photo, []))[1].append(hit)
        douban = douban_links(request, [(hit.spine_id, hit.spine) for hit in hits])
        return TEMPLATES.TemplateResponse(
            request, "search.html", {"q": q, "groups": list(groups.values()), "douban": douban}
        )

    @app.get("/photos/{photo_id}")
    def photo_page(request: Request, photo_id: int, q: str = ""):
        photo = library.get_photo(photo_id)
        hit_ids = {hit.spine_id for hit in library.search(q) if hit.photo.id == photo_id}
        spines = library.spines(photo_id)
        return TEMPLATES.TemplateResponse(
            request,
            "photo.html",
            {
                "photo": photo,
                "spines": spines,
                "douban": douban_links(request, spines),
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
    ):
        # 没重画框时四个坐标都不传，框保持原样；标签留空表示不改
        box = None if x1 is None else box_from_form(x1, y1, x2, y2)
        library.update_spine(spine_id, text=text, box=box, tags=split_tags(tags))
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
        ids = [i for i, _ in library.spines(photo_id)]
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
