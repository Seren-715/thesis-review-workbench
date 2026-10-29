import json
from pathlib import Path


def check_r001(text):
    """检索“我”字，保留位置和上下文。"""
    hits = []

    for position, character in enumerate(text):
        if character == "我":
            hits.append({
                "offset": position,
                "context": text[
                    max(0, position - 12): position + 13
                ],
            })

    return {
        "rule_id": "R001",
        "hit_count": len(hits),
        "hits": hits,
    }


if __name__ == "__main__":
    record = Path(
        r"D:\SVN虚拟助教\data\小批次实验记录.md"
    ).read_text(encoding="utf-8-sig")

    old_text = record.split(
        "r300 中被批注的结论原文：", 1
    )[1].split("当前稿的结论原文：", 1)[0].strip()

    current_text = record.split(
        "当前稿的结论原文：", 1
    )[1].strip()

    for name, text in [
        ("旧稿结论", old_text),
        ("当前稿结论", current_text),
    ]:
        print(name)
        print(json.dumps(
            check_r001(text),
            ensure_ascii=False,
            indent=2,
        ))