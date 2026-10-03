import io
import json
from dataclasses import dataclass
from functools import cache

import numpy as np
from openai import OpenAI
from PIL import Image

from shelf_finder import config

# 置信度低于这个值的文字行多是书脊上的花纹、条码之类，丢掉
MIN_LINE_SCORE = 0.5
# 两行的中心互相落在对方文字走向直线两侧这个距离以内（按较细那行的粗细计）才算同一条书脊
SAME_SPINE_OFFSET = 0.5
# 交给合并模型的坐标归一化到 0..999，与照片尺寸无关
NORM_SCALE = 999

MERGE_PROMPT = """下面是一张书架照片上 OCR 读出的文字段。每段是沿同一条书脊走向排列的几行文字，已经按位置切好，不同的段默认是不同的书。
每段给出编号、文字方向、轴对齐边框（坐标归一化到 0-999，原点在左上角）和段内文字。

有一种情况需要合并：书脊比较宽时，一本书的书名、副标题、英文书名、作者会并排印成几列，于是被切成了相邻的几段。
两段属于同一本书需要同时满足：在照片上紧挨着，沿书脊方向的范围相近；文字内容能连成一本书，比如中文书名和它的英文书名、被拆开的书名字、书名和它的作者。
同一套书的不同册、两本相同的书，各自是独立的书，不要合并。拿不准时不要合并。

输出照片上的全部书。每本书给出它包含的段编号，以及整理后的完整书脊文字：书名在前，再接作者、出版社等；修正明显的 OCR 误识字，保留原有语言。
每个段编号最多出现在一本书里。

"""
MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "books": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_ids": {"type": "array", "items": {"type": "integer"}},
                    "text": {"type": "string"},
                },
                "required": ["segment_ids", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["books"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int

    def union(self, other):
        return Box(min(self.x1, other.x1), min(self.y1, other.y1), max(self.x2, other.x2), max(self.y2, other.y2))


@dataclass(frozen=True, eq=False)
class TextLine:
    text: str
    # OCR 给出的四点斜框，照片像素坐标
    quad: np.ndarray

    @property
    def box(self):
        xs, ys = self.quad[:, 0], self.quad[:, 1]
        return Box(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))

    @property
    def center(self):
        return self.quad.mean(axis=0)

    def _edges(self):
        e1, e2 = self.quad[1] - self.quad[0], self.quad[3] - self.quad[0]
        return (e1, e2) if np.linalg.norm(e1) >= np.linalg.norm(e2) else (e2, e1)

    @property
    def axis(self):
        # 文字走向的单位向量，统一朝下（竖排）或朝右（横排），方便段内排序
        along = self._edges()[0]
        axis = along / np.linalg.norm(along)
        major = 1 if abs(axis[1]) > abs(axis[0]) else 0
        return axis if axis[major] > 0 else -axis

    @property
    def thickness(self):
        return float(np.linalg.norm(self._edges()[1]))

    def offset_from(self, other):
        # 本行中心到 other 文字走向直线的垂直距离
        d = self.center - other.center
        a = other.axis
        return abs(d[0] * a[1] - d[1] * a[0])


@dataclass(frozen=True)
class Segment:
    # 按文字走向从上到下（或从左到右）排好的行
    lines: list[TextLine]

    @property
    def text(self):
        return " ".join(line.text for line in self.lines)

    @property
    def box(self):
        box = self.lines[0].box
        for line in self.lines[1:]:
            box = box.union(line.box)
        return box

    @property
    def vertical(self):
        a = self.lines[0].axis
        return abs(a[1]) > abs(a[0])


@dataclass(frozen=True)
class Spine:
    text: str
    box: Box
    # 类型标签，取自固定的标签表；识别阶段还没有贴标签
    tags: tuple[str, ...] = ()


def recognize(image: bytes) -> list[Spine]:
    width, height = Image.open(io.BytesIO(image)).size
    segments = segment_lines(read_text_lines(image))
    if not segments:
        return []
    return merge_segments(segments, width, height)


@cache
def _ocr():
    # paddleocr 在可选依赖组 recognition 里，只查看数据的部署不装它，所以放到第一次识别时再导入
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang="ch",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        # 书脊文字常被旋转 90 或 180 度
        use_textline_orientation=True,
        # 本机 CPU 上开 oneDNN 会报 ConvertPirAttribute2RuntimeAttribute not support
        enable_mkldnn=False,
    )


def read_text_lines(image: bytes) -> list[TextLine]:
    img = Image.open(io.BytesIO(image)).convert("RGB")
    # PaddleOCR 按 OpenCV 惯例接收 BGR 顺序的数组
    bgr = np.asarray(img)[:, :, ::-1]
    (result,) = _ocr().predict(bgr)
    return [
        TextLine(text=text.strip(), quad=np.asarray(poly, dtype=float))
        for text, score, poly in zip(result["rec_texts"], result["rec_scores"], result["rec_polys"])
        if text.strip() and score >= MIN_LINE_SCORE
    ]


def segment_lines(lines: list[TextLine]) -> list[Segment]:
    # 书脊在照片上常是斜的：落在同一条文字走向直线上的行属于同一条书脊
    parent = list(range(len(lines)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(lines):
        for j in range(i + 1, len(lines)):
            b = lines[j]
            limit = min(a.thickness, b.thickness) * SAME_SPINE_OFFSET
            if a.offset_from(b) < limit and b.offset_from(a) < limit:
                parent[root(i)] = root(j)

    groups = {}
    for i, line in enumerate(lines):
        groups.setdefault(root(i), []).append(line)
    segments = []
    for members in groups.values():
        axis = max(members, key=lambda line: line.thickness).axis
        members.sort(key=lambda line: float(line.center @ axis))
        segments.append(Segment(members))
    segments.sort(key=lambda s: (s.box.x1, s.box.y1))
    return segments


def merge_segments(segments: list[Segment], width: int, height: int) -> list[Spine]:
    def norm(box):
        return (
            round(box.x1 / width * NORM_SCALE),
            round(box.y1 / height * NORM_SCALE),
            round(box.x2 / width * NORM_SCALE),
            round(box.y2 / height * NORM_SCALE),
        )

    description = "\n".join(
        "#{} {} x1={} y1={} x2={} y2={} text={!r}".format(i, "竖排" if s.vertical else "横排", *norm(s.box), s.text)
        for i, s in enumerate(segments)
    )
    client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)
    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": MERGE_PROMPT + description}],
        temperature=0,
        response_format={"type": "json_schema", "json_schema": {"name": "books", "schema": MERGE_SCHEMA}},
        # 开思考会长时间推理并耗尽输出上限，见 ADR 0001
        extra_body={"chat_template_kwargs": {"thinking": False}},
    )
    choice = response.choices[0]
    assert choice.finish_reason == "stop", choice.finish_reason
    books = json.loads(choice.message.content)["books"]

    used = [i for book in books for i in book["segment_ids"]]
    assert len(used) == len(set(used)), f"合并结果里有段编号重复：{sorted(used)}"
    assert all(0 <= i < len(segments) for i in used), f"合并结果里有不存在的段编号：{sorted(used)}"

    spines = []
    for book in books:
        if not book["segment_ids"]:
            continue
        box = segments[book["segment_ids"][0]].box
        for i in book["segment_ids"][1:]:
            box = box.union(segments[i].box)
        spines.append(Spine(text=book["text"].strip(), box=box))
    # 模型没提到的段各自就是一本书，文字保持 OCR 原样
    for i in sorted(set(range(len(segments))) - set(used)):
        spines.append(Spine(text=segments[i].text, box=segments[i].box))
    return spines
