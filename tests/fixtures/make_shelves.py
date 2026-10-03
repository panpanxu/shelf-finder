# 生成测试用的合成书架照片，以及每本书书脊的真实像素范围（ground truth）
# 运行：uv run python tests/fixtures/make_shelves.py
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
OUT = Path(__file__).parent

SHELVES = {
    "shelf_a": [
        ((180, 60, 60), "深入理解计算机系统", "机械工业出版社"),
        ((60, 90, 170), "统计学习方法", "李航 著"),
        ((70, 140, 90), "Designing Data-Intensive Applications", "O'Reilly"),
        ((150, 120, 40), "三体 全集", "刘慈欣"),
        ((90, 60, 130), "程序员修炼之道", "电子工业出版社"),
    ],
    "shelf_b": [
        ((40, 110, 120), "百年孤独", "南海出版公司"),
        ((170, 90, 50), "算法导论", "机械工业出版社"),
        ((60, 60, 60), "红楼梦", "人民文学出版社"),
        ((120, 50, 90), "人类简史", "中信出版社"),
    ],
}

W, H = 1200, 900
TOP, BOTTOM = 80, H - 80


def draw(books):
    img = Image.new("RGB", (W, H), (235, 225, 200))
    truth = []
    x = 60
    for color, title, sub in books:
        w = 170 if len(title) > 10 else 130
        img.paste(color, (x, TOP, x + w, BOTTOM))
        # 竖排文字：先在横向画布上画，再旋转 90 度贴到书脊上
        strip = Image.new("RGB", (BOTTOM - TOP, w), color)
        d = ImageDraw.Draw(strip)
        d.text((30, 18), title, font=ImageFont.truetype(FONT, 40), fill=(255, 255, 255))
        d.text((30, w - 46), sub, font=ImageFont.truetype(FONT, 24), fill=(230, 230, 230))
        img.paste(strip.rotate(-90, expand=True), (x, TOP))
        truth.append({"title": title, "sub": sub, "x1": x, "y1": TOP, "x2": x + w, "y2": BOTTOM})
        x += w + 12
    return img, truth


for name, books in SHELVES.items():
    img, truth = draw(books)
    img.save(OUT / f"{name}.jpg", quality=90)
    (OUT / f"{name}.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2))
