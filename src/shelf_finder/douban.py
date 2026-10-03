from urllib.parse import urlencode

import opencc
import regex

TO_SIMPLIFIED = opencc.OpenCC("t2s")

# 英文书名后面接的作者、译者常以方括号开头，例如「[美]」「【英】」
LATIN_TITLE = regex.compile(r"^[^\p{Han}\[【]+")
# 作者名前面的国籍标注：[美]、【英】、（法）、(德)，括号里一两个字
NATIONALITY = regex.compile(r"[\[【（(][^\]】）)]{1,2}[\]】）)]")
# 段落里汉字之前残留的英文（例如英文书名的最后一个词和中文作者粘在一起）
LEADING_LATIN = regex.compile(r"^[^\p{Han}]+")
# 书名后面括号里的版次、修订说明：（修订版）（原书第4版）
PARENTHESIZED = regex.compile(r"[（(][^（()）]*[)）]")
# 作者、译者、编者名字后面跟的身份词
ROLE_SUFFIX = regex.compile(r"(编著|主编|审校|著|编|译|校)$")
HAN = regex.compile(r"\p{Han}")


def douban_search_url(spine_text: str, mobile: bool = False) -> str:
    query = search_query(spine_text)
    if mobile:
        return "https://m.douban.com/search/?" + urlencode({"query": query, "type": "book"})
    # cat=1001 表示只搜图书
    return "https://search.douban.com/book/subject_search?" + urlencode({"search_text": query, "cat": "1001"})


def search_query(spine_text: str) -> str:
    # 豆瓣搜「书名 作者 出版社」结果最好；整段书脊原文（英文书名、版次、国籍、英文出版社都带上）会搜不到
    text = TO_SIMPLIFIED.convert(spine_text.strip())
    title, rest = split_title(text)
    parts = [title] if title else []
    for segment in rest.split():
        segment = PARENTHESIZED.sub("", segment)
        segment = NATIONALITY.sub("", segment)
        if not HAN.search(segment):
            continue
        segment = ROLE_SUFFIX.sub("", LEADING_LATIN.sub("", segment))
        if not segment or regex.search(r"\d", segment) or ("版" in segment and "出版" not in segment):
            continue
        if segment not in parts:
            parts.append(segment)
    return " ".join(parts)


def split_title(text: str) -> tuple[str, str]:
    # 书脊文字是「书名 作者 出版社」，书名在最前面。
    # 中文书名不含空格，取第一段；英文书名本身带空格，取开头一直到第一个汉字或方括号为止。
    # 以方括号开头（作者国籍排在最前）的分不出书名，全部按后面的段处理
    if HAN.match(text):
        title, _, rest = text.partition(" ")
        return PARENTHESIZED.sub("", title), rest
    latin = LATIN_TITLE.match(text)
    if latin is None:
        return "", text
    return latin.group().strip(), text[latin.end() :]
