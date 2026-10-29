"""Two narrowly scoped, read-only nanobot tools for the existing P001 initial draft.

Tools verify local provenance on every call. Only compact anonymous results are
returned to a model; the caller may save ``full_result`` as local evidence.
No API, SVN, credential access, image recognition or file writes occur here.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from nanobot.agent.tools.base import Tool, tool_parameters
from recheck_initial_pilot import collect_recheck


TOOL_NAMES = {
    "R001": "check_initial_conclusion",
    "R002": "check_initial_formula_labels",
}
LIMITS = (
    "仅检查P001保存的初稿及限定格式。图片内容、跨段图文关联、"
    "公式语义和渲染结果未核验；未命中不代表合格。"
)


@tool_parameters({
    "type": "object",
    "properties": {
        "sample_id": {
            "type": "string", "enum": ["P001"],
            "description": "唯一允许的匿名样本P001；读取已导出的r194初稿。",
        },
    },
    "required": ["sample_id"],
    "additionalProperties": False,
})
class InitialPaperTool(Tool):
    """Bind one verified initial-export directory to one local pilot rule."""

    _plugin_discoverable = False

    def __init__(self, rule_id: str, initial_dir: Path):
        if rule_id not in TOOL_NAMES:
            raise ValueError("rule_id must be R001 or R002")
        self.rule_id = rule_id
        self.initial_dir = Path(initial_dir).resolve()
        self.full_result: dict | None = None
        self.last_summary: dict | None = None

    @property
    def name(self) -> str:
        return TOOL_NAMES[self.rule_id]

    @property
    def description(self) -> str:
        if self.rule_id == "R001":
            return "核查P001 r194初稿结论中的我字线索，返回状态与位置；未命中不代表合格。"
        return (
            "核查P001 r194初稿保存的公式编号文字；图片旁编号仅作线索，"
            "partial表示部分覆盖，不代表公式全部合格。"
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, sample_id: str) -> str:
        # Clear cached evidence before validation; a failed call cannot reuse an
        # earlier successful result as if it belonged to the latest invocation.
        self.full_result = None
        self.last_summary = None
        if type(sample_id) is not str or sample_id != "P001":
            raise ValueError("only sample_id P001 is allowed")
        result = collect_recheck(self.initial_dir)
        source = result["source"]
        if source.get("sample_id") != "P001" or source.get("revision") != 194:
            raise ValueError("recheck source is not the expected P001 r194 initial draft")
        matches = [rule for rule in result["rules"] if rule.get("rule_id") == self.rule_id]
        if len(matches) != 1:
            raise ValueError("recheck must return exactly one selected rule")
        rule = matches[0]
        if self.rule_id == "R001":
            keys = (
                "rule_id", "status", "hit_count", "heading_paragraph",
                "end_heading_paragraph", "checked_paragraph_indices",
            )
            selected = {key: deepcopy(rule.get(key)) for key in keys}
            hits = rule.get("hits", [])
            selected["hits"] = deepcopy(hits[:3])
            selected["hits_truncated"] = len(hits) > 3
            if rule.get("reason"):
                selected["reason"] = rule["reason"]
        else:
            keys = (
                "rule_id", "status", "formula_label_count", "duplicate_group_count",
                "image_label_count", "unbound_label_count", "text_label_count",
                "text_duplicate_group_count",
            )
            selected = {key: deepcopy(rule[key]) for key in keys}
            labels = []
            for collection, label_type in (
                ("labels", "omml_definition"),
                ("image_labels", "image_label_needs_visual_review"),
                ("unbound_labels", "unbound_label_needs_layout_review"),
            ):
                for label in rule.get(collection, []):
                    labels.append({
                        "paragraph_index": label["paragraph_index"],
                        "label": label["label"], "type": label_type,
                    })
            labels.sort(key=lambda label: (label["paragraph_index"], label["label"]))
            selected["labels"] = labels[:10]
            selected["labels_truncated"] = len(labels) > 10
        summary = {
            "source": {key: source[key] for key in ("sample_id", "revision", "sha256")},
            "rule": selected, "limits": LIMITS,
        }
        self.full_result = deepcopy(result)
        self.last_summary = deepcopy(summary)
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
