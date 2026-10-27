import json
import shlex
import subprocess

import regex

from shelf_finder import config
from shelf_finder.douban import HAN, NATIONALITY, PARENTHESIZED, ROLE_SUFFIX, split_title
from shelf_finder.library import compact

# 出版机构名里常见的结尾
PUBLISHER = regex.compile(r"(出版社|出版公司|出版集团|书店|书局|书馆|出版中心|Press|Media)", regex.IGNORECASE)


def run_cli(*args: str) -> dict | list | None:
    # 调用 douban-cli，结果一律是 JSON。没查到时它退出码 1 并在标准错误里给
    # {"ok": false, "error": {"code": "NOT_FOUND"}}，这种当作没有结果；其他错误直接报错
    command = shlex.split(config.DOUBAN_CLI) + list(args) + ["--json"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if completed.returncode == 0:
        return json.loads(completed.stdout)
    error = json.loads(completed.stderr.strip() or completed.stdout.strip())
    if error.get("error", {}).get("code") in ("NOT_FOUND", "AMBIGUOUS"):
        return None
    raise RuntimeError(f"douban-cli 退出码 {completed.returncode}：{error}")


# 书脊上的书名常带这些后缀，影视作品名里没有
TITLE_SUFFIX = regex.compile(r"(全集|套装|合集|合订本|上下册|上下|精装版|平装版)$")


def parse_spine(spine_text: str) -> tuple[str, str, str]:
    # 书脊文字拆成书名、作者、出版社；拆不出来的留空
    title, rest = split_title(spine_text)
    author, publisher = "", ""
    for segment in rest.split():
        segment = NATIONALITY.sub("", PARENTHESIZED.sub("", segment))
        if not segment:
            continue
        if PUBLISHER.search(segment):
            publisher = publisher or segment
        elif HAN.search(segment) and not author:
            candidate = ROLE_SUFFIX.sub("", regex.sub(r"^[^\p{Han}]+", "", segment))
            if 2 <= len(candidate) <= 4:
                author = candidate
    return title, author, publisher


def find_subject(spine_text: str) -> str | None:
    # 书名要对得上，出版社要对得上，几版里取评价人数最多的那版；对不上就留空给人填
    title, _, publisher = parse_spine(spine_text)
    if not title:
        return None
    result = run_cli("book", "search", title, "--limit", "5")
    if not isinstance(result, list):
        return None
    wanted = compact(title)
    best = None
    for item in result:
        found = compact(item["title"])
        if wanted not in found and found not in wanted:
            continue
        if publisher and compact(publisher) not in compact(item["meta"]):
            continue
        count = int(item.get("rating_count") or 0)
        if best is None or count > best[0]:
            best = (count, item["url"])
    return best[1] if best else None


def find_adaptations(spine_text: str) -> list[tuple[str, str]]:
    # 同名影视只有在简介里能对上这本书时才算改编作品，光是片名相同不算
    title, author, _ = parse_spine(spine_text)
    title = TITLE_SUFFIX.sub("", PARENTHESIZED.sub("", title)).strip()
    if not title:
        return []
    result = run_cli("movie", title)
    if not isinstance(result, dict) or not result.get("summary"):
        return []
    # 简介里要有「改编」、书名号括起来的书名、或者书脊上的作者名，三样里至少一样
    summary = result["summary"]
    if not ("改编" in summary or f"《{title}》" in summary or (author and author in summary)):
        return []
    kind = "剧集" if result.get("type") == "tv" else "电影"
    label = f"{result['title']}（{result.get('year', '')} {kind}）".replace("（ ", "（")
    return [(label, result["url"])]
