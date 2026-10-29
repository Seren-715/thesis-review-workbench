"""Offline integration and refusal tests; uses mocked model responses, no API key."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from openai.types.chat import ChatCompletion
import three_rule_pilot_agent as agent
import verified_table_pilot as reader

REVIEW = Path(r"D:\svn_agent\data\svn_exports\cross_student_rules_20261007_121139_462691\reviewed_pilot_20261007_121835_788699")
PROOF = REVIEW / reader.RECEIPT_RELATIVE


def response(message, usage=True):
    return ChatCompletion.model_validate({"id": "offline-mock", "object": "chat.completion", "created": 0,
        "model": "offline", "choices": [{"index": 0, "message": message,
        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120} if usage else None})


class FakeModel:
    def __init__(self, mode):
        self.mode, self.requests, self.index = mode, [], 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **request):
        self.requests.append(request)
        if self.mode == "missing_usage":
            return response({"role": "assistant", "content": "unusable"}, False)
        if self.mode == "invalid_usage":
            answer = response({"role": "assistant", "content": "unusable"})
            answer.usage.total_tokens = 101
            return answer
        completed = {row["name"] for msg in request["messages"] if msg["role"] == "tool"
                     for row in [{"name": msg["tool_call_id"].split(":")[0]}]}
        remaining = sorted(agent.NAMES - completed)
        if remaining:
            names = remaining if self.mode != "sequential" else remaining[:1]
            if self.mode == "duplicate":
                names = [remaining[0], remaining[0]]
            self.index += 1
            return response({"role": "assistant", "content": None, "tool_calls": [
                {"id": name + ":" + str(self.index) + ":" + str(i), "type": "function",
                 "function": {"name": name, "arguments": "{}"}} for i, name in enumerate(names)]})
        evidence = {msg["tool_call_id"].split(":")[0]: json.loads(msg["content"])
                    for msg in request["messages"] if msg["role"] == "tool"}
        rows = []
        for i, first in enumerate(evidence["triage_three_initial_texts"]["items"]):
            second = evidence["inspect_three_initial_chapters"]["items"][i]
            third = evidence["load_verified_table_pagination"]["items"][i]
            rows.append({key: first[key] for key in ("sample_id", "revision", "raw_wo_count", "excluded_count", "retained_context_count")}
                        | {key: second[key] for key in ("structure_status", "relevant_heading_count")}
                        | {"table_status": third["table_status"]})
        if self.mode == "misattribute_teacher":
            rows[2]["revision"] = 249
            rows[2]["table_status"] = "confirmed_missing_continuation_label_in_markup_layout"
        final = {"items": rows, "teacher_reference": evidence["load_verified_table_pagination"]["teacher_reference"]}
        return response({"role": "assistant", "content": json.dumps(final, ensure_ascii=False)})


def main():
    real_collect = agent.collect
    real_triage = agent.collect_triage
    real_load = agent.load_verified_pagination
    # Verify actual local sources once. Temporary output directories contain only mock ledgers.
    reader.load_verified_pagination(REVIEW, PROOF)
    for mode in ("batched", "sequential", "misattribute_teacher", "duplicate", "missing_usage", "invalid_usage"):
        with tempfile.TemporaryDirectory(dir=r"D:\svn_agent\data") as temporary:
            output_root = Path(temporary)
            model = FakeModel(mode)
            with patch.object(agent, "collect", side_effect=lambda unused: real_collect(REVIEW)), \
                 patch.object(agent, "collect_triage", side_effect=lambda unused: real_triage(REVIEW)), \
                 patch.object(agent, "load_verified_pagination", side_effect=lambda unused, proof: real_load(REVIEW, proof)), \
                 contextlib.redirect_stdout(io.StringIO()):
                output, state = asyncio.run(agent.run_agent(output_root, model, PROOF))
            if mode in ("batched", "sequential"):
                assert state["status"] == "completed_verified_counts", state
                assert state["request_attempts"] == (2 if mode == "batched" else 4)
                assert len(state["tool_attempts"]) == 3 and all(r["status"] == "completed" for r in state["tool_attempts"])
                final = json.loads((output / "final_report.json").read_text(encoding="utf-8"))
                assert [r["table_status"] for r in final["items"]] == ["not_checked", "not_checked", "not_applicable_single_page"]
                assert final["items"][2]["revision"] == 246 and final["teacher_reference"]["revision"] == 249
            else:
                assert state["status"] == "failed_no_retry", state
                assert not (output / "final_report.json").exists()
                if mode == "duplicate":
                    assert state["tool_attempts"] == []
                if mode in ("missing_usage", "invalid_usage"):
                    assert state["unknown_cost_attempts"] == 1 and state["request_attempts"] == 1
                    assert (output / "response_01.json").exists()
            assert state["svn_requests"] == 0 and state["word_launches"] == 0
            assert len(model.requests) == state["request_attempts"]
        print("PASS offline mock:", mode)
    try:
        reader.load_verified_pagination(REVIEW, REVIEW / "table_pagination_20261007_142004_636108/visual_review/verification.json")
        raise AssertionError("Unaccepted receipt admitted")
    except ValueError:
        print("PASS reject unaccepted PDF-conflict receipt")
    real_digest = reader.digest
    victim = PROOF.parent / "r249_page24.png"
    with patch.object(reader, "digest", side_effect=lambda path: "0" * 64 if Path(path).resolve() == victim.resolve() else real_digest(path)):
        try:
            reader.load_verified_pagination(REVIEW, PROOF)
            raise AssertionError("Modified artifact admitted")
        except ValueError:
            print("PASS reject changed rendered evidence hash (mocked, original untouched)")
    print("All offline checks passed. No real LLM/SVN/Word requests; mock token counts are not actual costs.")


if __name__ == "__main__":
    main()
