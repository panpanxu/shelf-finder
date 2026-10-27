import regex

from shelf_finder.tagging import assign_tags, propose_vocabulary

VOCABULARY = [
    "小说", "写作", "中国历史", "世界历史", "哲学", "心理学", "社会科学", "古典文献", "诗词散文", "艺术设计",
    "电影戏剧", "自然博物", "科普", "数学", "数论", "几何", "微积分", "数学教育", "人物传记", "文化随笔",
    "生活实用", "其他",
]

SPINES = [
    "深入理解计算机系统 机械工业出版社",
    "统计学习方法 李航著",
    "Designing Data-Intensive Applications O'Reilly",
    "三体全集 刘慈欣",
    "程序员修炼之道 电子工业出版社",
    "百年孤独 南海出版公司",
    "算法导论 机械工业出版社",
    "红楼梦 人民文学出版社",
    "人类简史 中信出版社",
    "初等数论 上海科技教育出版社",
    "普林斯顿微积分读本 人民邮电出版社",
    "费马大定理 358年的一个困惑了世间智者",
]


def test_proposed_vocabulary_is_a_short_fixed_list_of_chinese_tags_ending_with_other():
    tags = propose_vocabulary(SPINES)

    assert 5 <= len(tags) <= 30
    assert len(set(tags)) == len(tags)
    assert tags[-1] == "其他"
    for tag in tags:
        assert 2 <= len(tag) <= 8 and regex.fullmatch(r"[\p{Han}A-Za-z]+", tag), tag
    # 这批书明显分成这几类，标签表里至少要能找到对应的词
    joined = " ".join(tags)
    assert ("小说" in joined or "文学" in joined) and ("数学" in joined or "数论" in joined)
    assert "计算机" in joined or "编程" in joined or "软件" in joined


def test_every_spine_gets_one_to_three_tags_taken_only_from_the_vocabulary():
    tagged = assign_tags(SPINES, VOCABULARY)

    assert len(tagged) == len(SPINES)
    for tags in tagged:
        assert 1 <= len(tags) <= 3 and len(set(tags)) == len(tags)
        assert set(tags) <= set(VOCABULARY), tags
    by_text = dict(zip(SPINES, tagged))
    assert "小说" in by_text["百年孤独 南海出版公司"]
    assert "数论" in by_text["初等数论 上海科技教育出版社"]
    assert "微积分" in by_text["普林斯顿微积分读本 人民邮电出版社"]
    # 这张标签表里没有计算机类，编程书只能归到「其他」
    assert by_text["程序员修炼之道 电子工业出版社"] == ["其他"]
