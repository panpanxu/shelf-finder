import io
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import opencc
import regex
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from shelf_finder.recognition import Box, Spine, recognize
from shelf_finder.tagging import OTHER, assign_tags

# iPhone 默认拍 HEIC，注册后 Pillow 才能打开
register_heif_opener()

# 关键字每这么多个字允许一处编辑（增、删、改），四个字以内必须逐字出现
CHARS_PER_ALLOWED_ERROR = 5
# 新书脊认领已有的书：全文比对，每这么多个字允许一处编辑，短书名必须完全一致
CHARS_PER_CLAIM_ERROR = 10
# 照片长边缩到这个像素数，取值依据见 ADR 0001
MAX_PHOTO_SIDE = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL
);
-- 书：独立于照片存在，标签、豆瓣链接、改编作品都挂在这里
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    -- 类型标签，顿号分隔，全部来自 tags 表
    tags TEXT NOT NULL DEFAULT '',
    douban_url TEXT NOT NULL DEFAULT '',
    -- 改编的影视作品，JSON 列表，每项 [标题, 链接]
    adaptations TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spines (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    -- 列表里的顺序，识别时按从左到右、从上到下编号，手工插入时在中间让位
    position INTEGER NOT NULL,
    -- 使用者手工改过文字或框、或者手工插入的行
    edited INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    x1 INTEGER NOT NULL,
    y1 INTEGER NOT NULL,
    x2 INTEGER NOT NULL,
    y2 INTEGER NOT NULL
);
-- 固定的标签表，使用者确认后写入，每本书只能从这里选标签
CREATE TABLE IF NOT EXISTS tags (
    position INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
"""
TAG_SEPARATOR = "、"
# 豆瓣上查过但没找到条目的书，链接列存这个标记，页面上当作没有链接
NO_SUBJECT = "-"


class PhotoNotFound(Exception):
    pass


class SpineNotFound(Exception):
    pass


class BookNotFound(Exception):
    pass


@dataclass(frozen=True)
class Photo:
    id: int
    name: str
    created_at: str
    width: int
    height: int


@dataclass(frozen=True)
class Book:
    id: int
    title: str
    tags: tuple[str, ...]
    douban_url: str
    # (标题, 链接)
    adaptations: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Hit:
    book: Book
    # 书当前所在的位置：指向它的书脊里照片最新的那条；不在任何照片上时两者为空
    photo: Photo | None
    spine_id: int | None
    spine: Spine | None
    # 关键字在书名里最佳匹配处的编辑次数，0 表示逐字包含
    errors: int


# 书脊上和关键字里都可能夹着繁体字，匹配前统一转成简体，逐字转换，混排也能处理
TO_SIMPLIFIED = opencc.OpenCC("t2s")


def compact(text):
    return TO_SIMPLIFIED.convert("".join(text.split()).lower())


def normalize_photo(image: bytes) -> bytes:
    # 手机照片的方向写在 EXIF 里，先转正再存，保证 OCR 和浏览器看到同一个方向
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image))).convert("RGB")
    # 识别与显示都用这张缩小后的图，书脊坐标就是它的像素坐标
    img.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


class Library:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.photo_dir = self.data_dir / "photos"
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "library.sqlite3"
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)
            self._migrate(db)

    def _migrate(self, db):
        columns = {row["name"] for row in db.execute("PRAGMA table_info(spines)")}
        # 第一版的 spines 表没有 position 和 edited 两列，补上并按原来的显示顺序编号
        if "position" not in columns:
            db.execute("ALTER TABLE spines ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
            db.execute("ALTER TABLE spines ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")
            rows = db.execute("SELECT id, photo_id FROM spines ORDER BY photo_id, x1, y1").fetchall()
            position = {}
            for row in rows:
                position[row["photo_id"]] = position.get(row["photo_id"], 0) + 1
                db.execute("UPDATE spines SET position = ? WHERE id = ?", (position[row["photo_id"]], row["id"]))
        # 书从书脊里拆出来之前，标签直接存在书脊上，每条书脊各自认领或新建一本书，标签随之搬到书上
        if "book_id" not in columns:
            db.execute("ALTER TABLE spines ADD COLUMN book_id INTEGER REFERENCES books(id)")
            old_tags = "tags" in columns
            rows = db.execute("SELECT * FROM spines ORDER BY photo_id, position").fetchall()
            for row in rows:
                tags = tuple(t for t in row["tags"].split(TAG_SEPARATOR) if t) if old_tags else ()
                book_id = self._claim_or_create(db, row["photo_id"], row["text"], tags)
                db.execute("UPDATE spines SET book_id = ? WHERE id = ?", (book_id, row["id"]))

    def _connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def photo_path(self, photo_id: int) -> Path:
        return self.photo_dir / f"{photo_id}.jpg"

    # ---- 书与认领 ----

    def _claim_or_create(self, db, photo_id: int, text: str, tags: tuple[str, ...] | None) -> int:
        # 新书脊认领书名相近的已有书；同一张照片上已经有书脊指向的书不能再认领，那是另一册
        needle = compact(text)
        pattern = regex.compile(
            "(?:%s){e<=%d}" % (regex.escape(needle), len(needle) // CHARS_PER_CLAIM_ERROR), regex.BESTMATCH
        )
        taken = {
            row["book_id"]
            for row in db.execute("SELECT book_id FROM spines WHERE photo_id = ? AND book_id IS NOT NULL", (photo_id,))
        }
        best = None
        for row in db.execute("SELECT id, title FROM books"):
            if row["id"] in taken:
                continue
            match = pattern.fullmatch(compact(row["title"]))
            if match is None:
                continue
            errors = sum(match.fuzzy_counts)
            if best is None or errors < best[1]:
                best = (row["id"], errors)
        if best is not None:
            return best[0]
        return db.execute(
            "INSERT INTO books (title, tags, created_at) VALUES (?, ?, ?)",
            (text, TAG_SEPARATOR.join(tags or ()), datetime.now().isoformat(timespec="seconds")),
        ).lastrowid

    def _place_spines(self, db, photo_id: int, spines: list[Spine], start_position: int = 1) -> list[int]:
        # 按位置排好的书脊逐条入库并认领书；新建的书批量贴标签
        spine_ids, new_books = [], []
        for position, spine in enumerate(spines, start_position):
            before = db.execute("SELECT max(id) FROM books").fetchone()[0] or 0
            book_id = self._claim_or_create(db, photo_id, spine.text, None)
            if book_id > before:
                new_books.append((book_id, spine.text))
            spine_ids.append(
                db.execute(
                    "INSERT INTO spines (photo_id, position, book_id, text, x1, y1, x2, y2) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (photo_id, position, book_id, spine.text, spine.box.x1, spine.box.y1, spine.box.x2, spine.box.y2),
                ).lastrowid
            )
        self._tag_books(db, new_books)
        return spine_ids

    def _tag_books(self, db, books: list[tuple[int, str]]) -> None:
        # 标签表还没确认时不贴标签
        vocabulary = self.vocabulary()
        if not vocabulary or not books:
            return
        tags = assign_tags([title for _, title in books], vocabulary)
        db.executemany(
            "UPDATE books SET tags = ? WHERE id = ?",
            [(TAG_SEPARATOR.join(t), book_id) for (book_id, _), t in zip(books, tags)],
        )

    def get_book(self, book_id: int) -> Book:
        with closing(self._connect()) as db:
            return _book(self._get_book(db, book_id))

    def _get_book(self, db, book_id):
        row = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise BookNotFound(book_id)
        return row

    def list_books(self) -> list[Book]:
        with closing(self._connect()) as db:
            return [_book(row) for row in db.execute("SELECT * FROM books ORDER BY id")]

    def update_book(
        self,
        book_id: int,
        tags: list[str] | None = None,
        douban_url: str | None = None,
        adaptations: list[tuple[str, str]] | None = None,
    ) -> None:
        with closing(self._connect()) as db, db:
            current = _book(self._get_book(db, book_id))
            new_tags = current.tags if tags is None else self._checked_tags(tags)
            new_url = current.douban_url if douban_url is None else douban_url.strip()
            assert not new_url or new_url.startswith("https://"), "豆瓣链接要以 https:// 开头"
            new_adaptations = current.adaptations if adaptations is None else tuple(
                (title.strip(), url.strip()) for title, url in adaptations if title.strip()
            )
            for _, url in new_adaptations:
                assert not url or url.startswith("https://"), "改编作品的链接要以 https:// 开头"
            db.execute(
                "UPDATE books SET tags = ?, douban_url = ?, adaptations = ? WHERE id = ?",
                (TAG_SEPARATOR.join(new_tags), new_url, json.dumps(new_adaptations, ensure_ascii=False), book_id),
            )

    def _checked_tags(self, tags: list[str]) -> tuple[str, ...]:
        cleaned = []
        for tag in tags:
            tag = tag.strip()
            if tag and tag not in cleaned:
                cleaned.append(tag)
        unknown = set(cleaned) - set(self.vocabulary())
        assert not unknown, f"标签表里没有：{'、'.join(sorted(unknown))}"
        assert 1 <= len(cleaned) <= 3, "每本书一到三个标签"
        return tuple(cleaned)

    def retag_photo(self, photo_id: int) -> None:
        # 按当前标签表给这张照片上全部的书重新贴标签，文字和框不动
        rows = self.spines(photo_id)
        with closing(self._connect()) as db, db:
            self._tag_books(db, [(book.id, book.title) for _, _, book in rows])

    def tag_counts(self) -> list[tuple[str, int]]:
        # 按标签表顺序给出每个标签下的书数，没有书的标签也列出来（计数为 0）
        counter = {tag: 0 for tag in self.vocabulary()}
        with closing(self._connect()) as db:
            for row in db.execute("SELECT tags FROM books"):
                for tag in row["tags"].split(TAG_SEPARATOR):
                    if tag in counter:
                        counter[tag] += 1
        return list(counter.items())

    def set_vocabulary(self, names: list[str]) -> None:
        names = [n.strip() for n in names if n.strip()]
        assert names and names[-1] == OTHER, "标签表最后一个必须是「其他」"
        assert len(set(names)) == len(names), "标签表里有重复"
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM tags")
            db.executemany("INSERT INTO tags (position, name) VALUES (?, ?)", list(enumerate(names, 1)))

    def vocabulary(self) -> list[str]:
        with closing(self._connect()) as db:
            return [row["name"] for row in db.execute("SELECT name FROM tags ORDER BY position")]

    # ---- 照片与书脊 ----

    def add_photo(self, name: str, image: bytes) -> Photo:
        name = name.strip()
        assert name, "照片名字不能为空"
        image = normalize_photo(image)
        width, height = Image.open(io.BytesIO(image)).size
        # 先识别再入库：识别失败时照片不留下任何记录
        spines = recognize(image)
        created_at = datetime.now().isoformat(timespec="seconds")
        with closing(self._connect()) as db, db:
            photo_id = db.execute(
                "INSERT INTO photos (name, created_at, width, height) VALUES (?, ?, ?, ?)",
                (name, created_at, width, height),
            ).lastrowid
            self._place_spines(db, photo_id, sorted(spines, key=lambda s: (s.box.x1, s.box.y1)))
            self.photo_path(photo_id).write_bytes(image)
        return Photo(photo_id, name, created_at, width, height)

    def _get_spine(self, db, spine_id):
        row = db.execute("SELECT * FROM spines WHERE id = ?", (spine_id,)).fetchone()
        if row is None:
            raise SpineNotFound(spine_id)
        return row

    def update_spine(self, spine_id: int, text: str | None = None, box: Box | None = None) -> None:
        # 改文字等于改正这本书的名字，书名跟着变
        with closing(self._connect()) as db, db:
            row = self._get_spine(db, spine_id)
            current = _spine(row)
            new_text = current.text if text is None else text.strip()
            new_box = current.box if box is None else box
            assert new_text, "书脊文字不能为空"
            db.execute(
                "UPDATE spines SET text = ?, x1 = ?, y1 = ?, x2 = ?, y2 = ?, edited = 1 WHERE id = ?",
                (new_text, new_box.x1, new_box.y1, new_box.x2, new_box.y2, spine_id),
            )
            db.execute("UPDATE books SET title = ? WHERE id = ?", (new_text, row["book_id"]))

    def insert_spine_after(self, spine_id: int, text: str, box: Box) -> int:
        text = text.strip()
        assert text, "书脊文字不能为空"
        with closing(self._connect()) as db, db:
            anchor = self._get_spine(db, spine_id)
            db.execute(
                "UPDATE spines SET position = position + 1 WHERE photo_id = ? AND position > ?",
                (anchor["photo_id"], anchor["position"]),
            )
            (new_id,) = self._place_spines(db, anchor["photo_id"], [Spine(text, box)], anchor["position"] + 1)
            db.execute("UPDATE spines SET edited = 1 WHERE id = ?", (new_id,))
            return new_id

    def delete_spine(self, spine_id: int) -> None:
        with closing(self._connect()) as db, db:
            self._get_spine(db, spine_id)
            db.execute("DELETE FROM spines WHERE id = ?", (spine_id,))
            self._prune_books(db)

    def _prune_books(self, db) -> None:
        # 使用者明确删掉书脊或照片后，没有任何书脊指向的书一起删掉；
        # 重新识别没读出来的书不走这里，留着等下次认领
        db.execute("DELETE FROM books WHERE id NOT IN (SELECT book_id FROM spines)")

    def manual_edits(self, photo_id: int) -> int:
        with closing(self._connect()) as db:
            return db.execute("SELECT count(*) FROM spines WHERE photo_id = ? AND edited = 1", (photo_id,)).fetchone()[0]

    def list_photos(self) -> list[Photo]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM photos ORDER BY id DESC").fetchall()
        return [_photo(row) for row in rows]

    def spine_counts(self) -> dict[int, int]:
        # 每张照片上的书脊数，按照片 id 索引
        with closing(self._connect()) as db:
            rows = db.execute("SELECT photo_id, count(*) AS n FROM spines GROUP BY photo_id").fetchall()
        return {row["photo_id"]: row["n"] for row in rows}

    def get_photo(self, photo_id: int) -> Photo:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if row is None:
            raise PhotoNotFound(photo_id)
        return _photo(row)

    def rename_photo(self, photo_id: int, name: str) -> None:
        name = name.strip()
        assert name, "照片名字不能为空"
        self.get_photo(photo_id)
        with closing(self._connect()) as db, db:
            db.execute("UPDATE photos SET name = ? WHERE id = ?", (name, photo_id))

    def spines(self, photo_id: int) -> list[tuple[int, Spine, Book]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT s.*, b.id AS book_id, b.title, b.tags, b.douban_url, b.adaptations"
                " FROM spines s JOIN books b ON b.id = s.book_id WHERE s.photo_id = ? ORDER BY s.position",
                (photo_id,),
            ).fetchall()
        return [(row["id"], _spine(row), _book(row, id_key="book_id")) for row in rows]

    def recognize_again(self, photo_id: int) -> None:
        self.get_photo(photo_id)
        spines = recognize(self.photo_path(photo_id).read_bytes())
        with closing(self._connect()) as db, db:
            # 旧书脊整体换掉；书留着，新书脊按书名认领回来，豆瓣链接和改编作品不丢
            db.execute("DELETE FROM spines WHERE photo_id = ?", (photo_id,))
            self._place_spines(db, photo_id, sorted(spines, key=lambda s: (s.box.x1, s.box.y1)))

    def delete_photo(self, photo_id: int) -> None:
        self.get_photo(photo_id)
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
            self._prune_books(db)
        self.photo_path(photo_id).unlink()

    def sync_douban(self, delay: float = 3.0, limit: int | None = None, log=lambda message: None) -> dict[str, int]:
        # 给还没有豆瓣条目链接的书查条目和改编作品，每本书查两次网页，所以请求之间停一会儿；
        # 查过的书不再查（查不到条目的也记一个标记，免得每次都重查）
        from shelf_finder.douban_sync import RateLimited, find_adaptations, find_subject

        with closing(self._connect()) as db:
            pending_ids = [row["id"] for row in db.execute("SELECT id FROM books WHERE douban_url = '' ORDER BY id")]
        pending = [book for book in self.list_books() if book.id in set(pending_ids)]
        if limit is not None:
            pending = pending[:limit]
        counts = {"looked_up": 0, "subjects": 0, "adaptations": 0, "remaining": len(pending), "rate_limited": False}
        for index, book in enumerate(pending):
            if index and delay:
                time.sleep(delay)
            try:
                subject = find_subject(book.title)
                adaptations = find_adaptations(book.title)
            except RateLimited:
                # 豆瓣暂时拒绝了，已经查完的都存好了，下次从这本接着查
                counts["rate_limited"] = True
                log(f"豆瓣限流，先停在《{book.title}》，稍后再跑")
                break
            counts["remaining"] -= 1
            counts["looked_up"] += 1
            counts["subjects"] += bool(subject)
            counts["adaptations"] += len(adaptations)
            with closing(self._connect()) as db, db:
                db.execute(
                    "UPDATE books SET douban_url = ?, adaptations = ? WHERE id = ?",
                    (subject or NO_SUBJECT, json.dumps(adaptations, ensure_ascii=False), book.id),
                )
            log(f"{book.title} → {'条目' if subject else '没找到条目'}，{len(adaptations)} 部改编作品")
        return counts

    # ---- 搜索 ----

    def search_books(self, keyword: str) -> list[tuple[Book, int]]:
        # 关键字正好是某个标签名时，贴着这个标签的书全部算命中，再加上书名匹配到的
        needle = compact(keyword)
        if not needle:
            return []
        pattern = regex.compile(
            "(?:%s){e<=%d}" % (regex.escape(needle), len(needle) // CHARS_PER_ALLOWED_ERROR),
            regex.BESTMATCH,
        )
        tag = next((t for t in self.vocabulary() if compact(t) == needle), None)
        matched = []
        for book in self.list_books():
            if tag is not None and tag in book.tags:
                matched.append((book, 0))
                continue
            match = pattern.search(compact(book.title))
            if match:
                matched.append((book, sum(match.fuzzy_counts)))
        return matched

    def search(self, keyword: str) -> list[Hit]:
        # 每本命中的书给出它当前的位置：指向它的书脊里照片最新的那条
        hits = []
        with closing(self._connect()) as db:
            for book, errors in self.search_books(keyword):
                row = db.execute(
                    "SELECT s.id AS spine_id, s.text, s.x1, s.y1, s.x2, s.y2,"
                    " p.id, p.name, p.created_at, p.width, p.height"
                    " FROM spines s JOIN photos p ON p.id = s.photo_id"
                    " WHERE s.book_id = ? ORDER BY p.id DESC, s.position LIMIT 1",
                    (book.id,),
                ).fetchone()
                if row is None:
                    hits.append(Hit(book, None, None, None, errors))
                else:
                    hits.append(Hit(book, _photo(row), row["spine_id"], _spine(row), errors))
        hits.sort(key=lambda h: (h.errors, h.photo.id if h.photo else 0, h.spine_id or 0))
        return hits


def _photo(row):
    return Photo(row["id"], row["name"], row["created_at"], row["width"], row["height"])


def _spine(row):
    return Spine(row["text"], Box(row["x1"], row["y1"], row["x2"], row["y2"]))


def _book(row, id_key="id"):
    tags = tuple(t for t in row["tags"].split(TAG_SEPARATOR) if t)
    adaptations = tuple((title, url) for title, url in json.loads(row["adaptations"]))
    douban_url = "" if row["douban_url"] == NO_SUBJECT else row["douban_url"]
    return Book(row[id_key], row["title"], tags, douban_url, adaptations)
