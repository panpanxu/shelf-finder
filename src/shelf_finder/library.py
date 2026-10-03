import io
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
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
CREATE TABLE IF NOT EXISTS spines (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    -- 列表里的顺序，识别时按从左到右、从上到下编号，手工插入时在中间让位
    position INTEGER NOT NULL,
    -- 使用者手工改过文字或框、或者手工插入的行
    edited INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    -- 类型标签，顿号分隔，全部来自 tags 表
    tags TEXT NOT NULL DEFAULT '',
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


class PhotoNotFound(Exception):
    pass


class SpineNotFound(Exception):
    pass


@dataclass(frozen=True)
class Photo:
    id: int
    name: str
    created_at: str
    width: int
    height: int


@dataclass(frozen=True)
class Hit:
    photo: Photo
    spine_id: int
    spine: Spine
    # 关键字在书脊文字里最佳匹配处的编辑次数，0 表示逐字包含
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
        # 第一版的 spines 表没有 position 和 edited 两列，补上并按原来的显示顺序编号
        columns = {row["name"] for row in db.execute("PRAGMA table_info(spines)")}
        if "tags" not in columns:
            db.execute("ALTER TABLE spines ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
        if "position" in columns:
            return
        db.execute("ALTER TABLE spines ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE spines ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")
        rows = db.execute("SELECT id, photo_id FROM spines ORDER BY photo_id, x1, y1").fetchall()
        position = {}
        for row in rows:
            position[row["photo_id"]] = position.get(row["photo_id"], 0) + 1
            db.execute("UPDATE spines SET position = ? WHERE id = ?", (position[row["photo_id"]], row["id"]))

    def _connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def photo_path(self, photo_id: int) -> Path:
        return self.photo_dir / f"{photo_id}.jpg"

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
            self._insert_spines(db, photo_id, spines)
            self.photo_path(photo_id).write_bytes(image)
        return Photo(photo_id, name, created_at, width, height)

    def _insert_spines(self, db, photo_id, spines):
        ordered = self._tagged(sorted(spines, key=lambda s: (s.box.x1, s.box.y1)))
        db.executemany(
            "INSERT INTO spines (photo_id, position, text, tags, x1, y1, x2, y2) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (photo_id, i, s.text, TAG_SEPARATOR.join(s.tags), s.box.x1, s.box.y1, s.box.x2, s.box.y2)
                for i, s in enumerate(ordered, 1)
            ],
        )

    def _tagged(self, spines: list[Spine]) -> list[Spine]:
        # 标签表还没确认时不贴标签
        vocabulary = self.vocabulary()
        if not vocabulary or not spines:
            return spines
        tags = assign_tags([s.text for s in spines], vocabulary)
        return [replace(s, tags=tuple(t)) for s, t in zip(spines, tags)]

    def retag_photo(self, photo_id: int) -> None:
        # 按当前标签表给这张照片的全部书脊重新贴标签，文字和框不动
        rows = self.spines(photo_id)
        tagged = self._tagged([spine for _, spine in rows])
        with closing(self._connect()) as db, db:
            db.executemany(
                "UPDATE spines SET tags = ? WHERE id = ?",
                [(TAG_SEPARATOR.join(spine.tags), spine_id) for (spine_id, _), spine in zip(rows, tagged)],
            )

    def tag_counts(self) -> list[tuple[str, int]]:
        # 按标签表顺序给出每个标签下的书数，没有书的标签也列出来（计数为 0）
        counter = {tag: 0 for tag in self.vocabulary()}
        with closing(self._connect()) as db:
            for row in db.execute("SELECT tags FROM spines"):
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

    def _get_spine(self, db, spine_id):
        row = db.execute("SELECT * FROM spines WHERE id = ?", (spine_id,)).fetchone()
        if row is None:
            raise SpineNotFound(spine_id)
        return row

    def update_spine(
        self, spine_id: int, text: str | None = None, box: Box | None = None, tags: list[str] | None = None
    ) -> None:
        with closing(self._connect()) as db, db:
            row = self._get_spine(db, spine_id)
            current = _spine(row)
            new_text = current.text if text is None else text.strip()
            new_box = current.box if box is None else box
            new_tags = current.tags if tags is None else self._checked_tags(tags)
            assert new_text, "书脊文字不能为空"
            db.execute(
                "UPDATE spines SET text = ?, tags = ?, x1 = ?, y1 = ?, x2 = ?, y2 = ?, edited = 1 WHERE id = ?",
                (new_text, TAG_SEPARATOR.join(new_tags), new_box.x1, new_box.y1, new_box.x2, new_box.y2, spine_id),
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

    def insert_spine_after(self, spine_id: int, text: str, box: Box) -> int:
        text = text.strip()
        assert text, "书脊文字不能为空"
        # 手工插入的书也自动贴标签，不满意再手工改
        (spine,) = self._tagged([Spine(text, box)])
        with closing(self._connect()) as db, db:
            anchor = self._get_spine(db, spine_id)
            db.execute(
                "UPDATE spines SET position = position + 1 WHERE photo_id = ? AND position > ?",
                (anchor["photo_id"], anchor["position"]),
            )
            return db.execute(
                "INSERT INTO spines (photo_id, position, edited, text, tags, x1, y1, x2, y2)"
                " VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    anchor["photo_id"],
                    anchor["position"] + 1,
                    text,
                    TAG_SEPARATOR.join(spine.tags),
                    box.x1,
                    box.y1,
                    box.x2,
                    box.y2,
                ),
            ).lastrowid

    def delete_spine(self, spine_id: int) -> None:
        with closing(self._connect()) as db, db:
            self._get_spine(db, spine_id)
            db.execute("DELETE FROM spines WHERE id = ?", (spine_id,))

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

    def spines(self, photo_id: int) -> list[tuple[int, Spine]]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM spines WHERE photo_id = ? ORDER BY position", (photo_id,)).fetchall()
        return [(row["id"], _spine(row)) for row in rows]

    def recognize_again(self, photo_id: int) -> None:
        self.get_photo(photo_id)
        spines = recognize(self.photo_path(photo_id).read_bytes())
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM spines WHERE photo_id = ?", (photo_id,))
            self._insert_spines(db, photo_id, spines)

    def delete_photo(self, photo_id: int) -> None:
        self.get_photo(photo_id)
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        self.photo_path(photo_id).unlink()

    def search(self, keyword: str) -> list[Hit]:
        needle = compact(keyword)
        if not needle:
            return []
        pattern = regex.compile(
            "(?:%s){e<=%d}" % (regex.escape(needle), len(needle) // CHARS_PER_ALLOWED_ERROR),
            regex.BESTMATCH,
        )
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT s.id AS spine_id, s.text, s.tags, s.x1, s.y1, s.x2, s.y2,"
                " p.id, p.name, p.created_at, p.width, p.height"
                " FROM spines s JOIN photos p ON p.id = s.photo_id"
            ).fetchall()
        # 关键字正好是某个标签名时，贴着这个标签的书全部算命中，再加上文字匹配到的
        tag = next((t for t in self.vocabulary() if compact(t) == needle), None)
        hits = []
        for row in rows:
            spine = _spine(row)
            if tag is not None and tag in spine.tags:
                hits.append(Hit(_photo(row), row["spine_id"], spine, 0))
                continue
            match = pattern.search(compact(row["text"]))
            if match:
                hits.append(Hit(_photo(row), row["spine_id"], spine, sum(match.fuzzy_counts)))
        hits.sort(key=lambda h: (h.errors, h.photo.id, h.spine_id))
        return hits


def _photo(row):
    return Photo(row["id"], row["name"], row["created_at"], row["width"], row["height"])


def _spine(row):
    tags = tuple(t for t in row["tags"].split(TAG_SEPARATOR) if t)
    return Spine(row["text"], Box(row["x1"], row["y1"], row["x2"], row["y2"]), tags)
