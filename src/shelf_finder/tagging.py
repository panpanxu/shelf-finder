import json

from openai import OpenAI

from shelf_finder import config

OTHER = "其他"

VOCABULARY_PROMPT = """下面是一个人全部藏书的书脊文字，每行一本（书名 作者 出版社）。
请为这批书设计一套固定的类型标签表，之后每本书会从这张表里选一到三个标签。

要求：
1. 标签数量在 10 到 25 个之间，按这批书的实际分布来定：书多的领域分细一点（比如数学书多就分出「数论」「几何」「微积分」），书少的领域合并成一个大类。
2. 每个标签两到六个汉字，是通用的类型名，不是具体书名或丛书名；同一个意思只保留一种写法。
3. 标签之间尽量不重叠，一本书通常能明确归到其中一两个。
4. 最后一个标签固定是「其他」，留给不属于任何一类的书。

只输出标签表。

"""
VOCABULARY_SCHEMA = {
    "type": "object",
    "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
    "required": ["tags"],
    "additionalProperties": False,
}


def _client():
    return OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)


def _ask(prompt: str, schema: dict, name: str) -> dict:
    response = _client().chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
        # 开思考会长时间推理并耗尽输出上限，见 ADR 0001
        extra_body={"chat_template_kwargs": {"thinking": False}},
    )
    choice = response.choices[0]
    assert choice.finish_reason == "stop", choice.finish_reason
    return json.loads(choice.message.content)


ASSIGN_PROMPT = """下面是一批书的书脊文字（书名 作者 出版社），每行一本，前面是编号。
请给每本书从固定的标签表里选一到三个类型标签。

标签表（只能用这里面的词，一个字都不能改）：
{vocabulary}

规则：
1. 根据书名、作者、出版社判断这本书的类型，选最贴切的一到三个标签，最贴切的放在前面。
2. 标签表里确实没有合适类型的书，只给「其他」这一个标签；给了别的标签就不要再给「其他」。
3. 每本书都要有结果，编号不能漏、不能重复。

"""
ASSIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "books": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}}},
                "required": ["id", "tags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["books"],
    "additionalProperties": False,
}


def assign_tags(spine_texts: list[str], vocabulary: list[str]) -> list[list[str]]:
    # 每本书一到三个标签，全部来自标签表；模型给出的表外词和重复词丢掉，丢空了就是「其他」
    if not spine_texts:
        return []
    prompt = ASSIGN_PROMPT.format(vocabulary="、".join(vocabulary))
    prompt += "\n".join(f"#{i} {text}" for i, text in enumerate(spine_texts))
    result = _ask(prompt, ASSIGN_SCHEMA, "book_tags")
    allowed = set(vocabulary)
    by_id = {}
    for book in result["books"]:
        tags = []
        for tag in book["tags"]:
            tag = tag.strip()
            if tag in allowed and tag != OTHER and tag not in tags:
                tags.append(tag)
        by_id[book["id"]] = tags[:3] or [OTHER]
    return [by_id.get(i, [OTHER]) for i in range(len(spine_texts))]


def propose_vocabulary(spine_texts: list[str]) -> list[str]:
    # 让模型通读全部书脊，提出一套标签表；去重后保证「其他」在最后
    result = _ask(VOCABULARY_PROMPT + "\n".join(spine_texts), VOCABULARY_SCHEMA, "vocabulary")
    tags = []
    for tag in result["tags"]:
        tag = tag.strip()
        if tag and tag != OTHER and tag not in tags:
            tags.append(tag)
    assert tags, "模型没有提出任何标签"
    return tags + [OTHER]
