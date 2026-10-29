import asyncio
import json
from pathlib import Path

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.registry import ToolRegistry
from pilot_tools import check_r001


@tool_parameters({
    "type": "object",
    "properties": {
        "version": {
            "type": "string",
            "enum": ["old", "current"],
            "description": "old 为旧稿，current 为当前稿",
        }
    },
    "required": ["version"],
    "additionalProperties": False,
})
class ConclusionCheckTool(Tool):
    @property
    def name(self):
        return "check_conclusion_r001"

    @property
    def description(self):
        return "检查 P001 指定版本的结论，返回我字的命中位置和上下文。"

    @property
    def read_only(self):
        return True

    async def execute(self, version):
        record = Path(
            r"D:\SVN虚拟助教\data\小批次实验记录.md"
        ).read_text(encoding="utf-8-sig")

        if version == "old":
            text = record.split(
                "r300 中被批注的结论原文：", 1
            )[1].split("当前稿的结论原文：", 1)[0].strip()
        elif version == "current":
            text = record.split(
                "当前稿的结论原文：", 1
            )[1].strip()
        else:
            raise ValueError("版本只能是 old 或 current")

        result = check_r001(text)
        result["sample_id"] = "P001"
        result["version"] = version
        return json.dumps(result, ensure_ascii=False)


async def main():
    registry = ToolRegistry()
    registry.register(ConclusionCheckTool())

    print("已注册工具：", registry.tool_names)

    for version in ["old", "current"]:
        result = await registry.execute(
            "check_conclusion_r001",
            {"version": version},
        )
        print(result)


if __name__ == "__main__":
    asyncio.run(main())