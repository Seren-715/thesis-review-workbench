"""One user-triggered API request for three source-bound comment examples."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from pilot_rule_analysis import EXPECTED_ITEM_COUNTS, build_payload, validate_response


MODEL = "deepseek-flash"
MAX_OUTPUT_TOKENS = 1800


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def candidate_report(rules: list[dict], payload: dict, run: dict) -> str:
    lines = [
        "# 小批次批注分类与候选检查项",
        "",
        "本轮仅选取 P001 的 3 条批注，覆盖范围为部分问题；未分析全部 14 条文字批注。",
        "检查项均未启用。格式与逐字引文校验通过，不代表已经核实其语义、适用范围或常见性。",
        "只有 1 名学生的证据，不能据此认定为常见错误。图片未提交模型，字体与排版结论仍需视觉核验。",
        "仅提炼五个指定问题，其他结论写作建议留待后续；两条公式编号记录是同一学生的关联证据。",
        "",
        f"模型：{run['model']}；实际 token 用量：{run['usage']['total_tokens']}。",
        "本轮限制一次请求和单次输出；尚未实现严格的任务总 token 预算。",
    ]
    for rule in rules:
        source = rule["source"]
        lines.extend([
            "",
            f"## {rule['candidate_id']}：{rule['category']}",
            "",
            f"- 来源：{source['sample_id']} / r{source['revision']} / {rule['ref']}",
            f"- 状态：{rule['status']}；核查方式：{rule['check_mode']}",
            f"- 检查项：{rule['check_item']}",
            f"- 范围：{rule['scope']}",
            f"- 方法：{rule['check_method']}",
            f"- 解释：{rule['reason']}",
            f"- 教师原话：{json.dumps(rule['teacher_quote'], ensure_ascii=False)}",
            f"- 论文原文：{json.dumps(rule['original_quote'], ensure_ascii=False)}",
            f"- 证据 ID：`{rule['evidence_id']}`",
        ])
    lines.extend(["", "完整来源、原始响应、输入和用量见同目录 JSON。", ""])
    return "\n".join(lines)


def execute_run(evidence_dir: Path, payload: dict, client: OpenAI) -> tuple[int, Path, dict]:
    """Write an audit trail, issue one request, and stop on any failure."""
    output = evidence_dir / ("rule_analysis_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(parents=False, exist_ok=False)
    run = {
        "created_at": datetime.now().astimezone().isoformat(),
        "model": MODEL,
        "status": "prepared",
        "max_request_attempts": 1,
        "request_attempts": 0,
        "responses_received": 0,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "automatic_retries": 0,
        "thinking": "disabled",
        "strict_total_token_budget": False,
        "selected_comment_count": len(payload["items"]),
        "response_contract_version": payload["response_contract_version"],
        "expected_item_counts": EXPECTED_ITEM_COUNTS,
        "max_candidate_items": sum(EXPECTED_ITEM_COUNTS.values()),
        "issue_coverage": "partial/capped",
        "independent_student_count": 1,
        "activation_status": "not_activated",
        "usage": None,
        "usage_status": "not_requested",
        "finish_reason": None,
        "evidence_directory": str(evidence_dir),
        "output_directory": str(output),
    }
    save_json(output / "request.json", {
        "model": MODEL,
        "messages": payload["messages"],
        "response_format": {"type": "json_object"},
        "max_tokens": MAX_OUTPUT_TOKENS,
        "extra_body": {"thinking": {"type": "disabled"}},
        "selected_items": payload["items"],
        "source_mapping": payload["source_mapping"],
        "input_integrity": payload["input_integrity"],
        "issue_coverage": "partial/capped",
        "response_contract_version": payload["response_contract_version"],
        "expected_item_counts": EXPECTED_ITEM_COUNTS,
    })
    save_json(output / "run.json", run)
    try:
        run.update(status="request_started", request_attempts=1, usage_status="unknown")
        # Persist before dispatch. A crash/timeout must not be reported as zero cost.
        save_json(output / "run.json", run)
        response = client.chat.completions.create(
            model=MODEL,
            messages=payload["messages"],
            response_format={"type": "json_object"},
            max_tokens=MAX_OUTPUT_TOKENS,
            extra_body={"thinking": {"type": "disabled"}},
        )
        run["responses_received"] = 1
        if response.usage is not None:
            usage = response.usage
            run["usage"] = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            values = list(run["usage"].values())
            run["usage_status"] = "reported" if (
                all(type(value) is int and value >= 0 for value in values)
                and usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
            ) else "invalid_returned_usage"
        save_json(output / "response.json", response.model_dump(mode="json"))
        if len(response.choices) != 1:
            raise ValueError("Expected exactly one model choice")
        choice = response.choices[0]
        run["finish_reason"] = choice.finish_reason
        if choice.finish_reason != "stop":
            raise ValueError(f"Model did not finish normally: {choice.finish_reason}")
        if run["usage_status"] != "reported":
            raise ValueError("API returned missing/invalid token usage; stop without another request")
        if choice.message.tool_calls:
            raise ValueError("Unexpected tool calls in a JSON-only classification request")
        rules = validate_response(choice.message.content, payload)
        save_json(output / "candidate_rules.json", {
            "issue_coverage": "partial/capped",
            "coverage_note": payload.get("coverage_note", "Only selected issues are analyzed."),
            "semantic_support_verified": False,
            "activation_status": "not_activated",
            "independent_student_count": 1,
            "response_contract_version": payload["response_contract_version"],
            "items": rules,
        })
        (output / "report.md").write_text(candidate_report(rules, payload, run), encoding="utf-8")
        run.update(status="validated_candidates", candidate_count=len(rules))
        result = 0
    except Exception as exc:
        # Do not persist client configuration, credentials, or raw HTTP exception bodies.
        run["status"] = "failed_no_retry"
        run["error_type"] = type(exc).__name__
        if isinstance(exc, ValueError):
            run["validation_error"] = str(exc)
        http_status = getattr(exc, "status_code", None)
        if http_status is not None:
            run["http_status"] = http_status
        result = 1
    finally:
        run["finished_at"] = datetime.now().astimezone().isoformat()
        save_json(output / "run.json", run)
    return result, output, run


def main() -> int:
    parser = argparse.ArgumentParser(description="仅对选定的三条批注执行一次候选检查项分析。")
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args()
    evidence_dir = args.evidence_dir.resolve()
    try:
        payload = build_payload(evidence_dir)
        if sum(len(message["content"]) for message in payload["messages"]) > 16000:
            raise ValueError("Input exceeds this pilot's character cap (not a token count)")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("输入核验失败，尚未请求模型：", exc)
        return 2
    key = os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek API Key（仅本次使用）：")
    if not key.strip():
        print("API Key 为空，尚未请求模型。")
        return 2
    client = OpenAI(api_key=key, base_url="https://api.deepseek.com", timeout=60, max_retries=0)
    print(f"来源已核验；选定 {len(payload['items'])} 条批注的 5 个目标问题，最多请求 1 次。")
    result, output, run = execute_run(evidence_dir, payload, client)
    client.close()
    print("状态：", run["status"])
    if run["usage"] is not None:
        print("输入 token：", run["usage"]["prompt_tokens"])
        print("输出 token：", run["usage"]["completion_tokens"])
        print("总 token：", run["usage"]["total_tokens"])
    else:
        print("token 用量未返回，成本未知；未自动重试。")
    if result == 0:
        print("候选检查项：", run["candidate_count"], "条（尚未启用）")
        print("可读报告：", output / "report.md")
    else:
        print("本次已停止，请保留日志；不要立即重复运行。")
        if "validation_error" in run:
            print("校验信息：", run["validation_error"])
        else:
            print("错误类型：", run.get("error_type"), "；HTTP 状态：", run.get("http_status", "未返回"))
    print("结果目录：", output)
    print("运行记录：", output / "run.json")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
