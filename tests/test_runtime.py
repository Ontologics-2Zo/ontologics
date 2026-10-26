"""Scripted adapter tests only: no live model/provider quality claim."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from tests.db import database_for_test
from ontologics.database import in_transaction

from ontologics.runtime import ModelConfig, ModelRuntime
from ontologics.service import AgentGateway, Service


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = database_for_test(self)
        self.service = Service(self.path, principals={
            "agent": {"kind": "agent", "roles": ["read", "draft"]},
            "reviewer": {"kind": "human", "roles": ["read", "review", "apply"]},
        })
        self.messages = [{"role": "system", "content": "Use shared tools."}, {"role": "user", "content": "private request 937"}]

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def runtime(self, provider, **config):
        return ModelRuntime(self.service, "agent", provider, config=ModelConfig(model="fixed-test-model", **config))

    def steps(self, result):
        return [dict(r) for r in self.service.db.execute("SELECT * FROM runtime_steps WHERE runtime_id=%s ORDER BY sequence", (result["run_id"],))]

    def test_fixed_model_tool_sequence_and_persistent_private_trace(self):
        seen = []
        def provider(*, model, messages):
            self.assertFalse(in_transaction(self.service.db))
            seen.append({"model": model, "messages": copy.deepcopy(messages)})
            messages[0]["content"] = "adapter mutation must not escape"
            if len(seen) == 1:
                return {"text": "조회", "tool_calls": [{"name": "lookup", "arguments": {"kind": "ProductModel"}}]}
            self.assertEqual("Use shared tools.", seen[-1]["messages"][0]["content"])
            self.assertEqual("tool", seen[-1]["messages"][-1]["role"])
            return {"text": "No registered products.", "tool_calls": []}
        original = copy.deepcopy(self.messages)
        result = self.runtime(provider).run(self.messages)
        self.assertEqual("completed", result["status"])
        self.assertEqual(2, result["steps"])
        self.assertEqual(["fixed-test-model", "fixed-test-model"], [x["model"] for x in seen])
        self.assertEqual(original, self.messages)
        self.assertEqual(1, len(result["tool_run_ids"]))
        trace = self.steps(result)
        self.assertEqual(["model", "tool", "model"], [x["kind"] for x in trace])
        self.assertTrue(all(x["finished_at"] for x in trace))
        persisted = dict(self.service.db.execute("SELECT * FROM runtime_runs WHERE id=%s", (result["run_id"],)).fetchone())
        self.assertEqual("fixed-test-model", json.loads(persisted["config"])["model"])
        self.assertEqual(sorted(AgentGateway.ALLOWED), json.loads(persisted["config"])["allowed_tools"])
        self.assertNotIn("private request 937", json.dumps(persisted))
        self.assertNotIn("No registered products.", json.dumps(persisted))
        self.service.close()
        self.service = Service(self.path, principals={"agent": {"kind": "agent", "roles": ["read", "draft"]}})
        self.assertEqual("completed", self.service.db.execute("SELECT status FROM runtime_runs WHERE id=%s", (result["run_id"],)).fetchone()['status'])

    def test_transient_model_failure_retries_exactly_once(self):
        calls = []
        def provider(**kwargs):
            calls.append(copy.deepcopy(kwargs))
            if len(calls) == 1:
                raise TimeoutError("must not persist private provider detail")
            return {"text": "finished", "tool_calls": []}
        result = self.runtime(provider).run(self.messages)
        self.assertTrue(result["ok"])
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(["failed", "completed"], [x["status"] for x in self.steps(result)])
        self.assertNotIn("private provider detail", json.dumps(self.steps(result)))

    def test_retry_exhaustion_and_nontransient_errors_are_terminal(self):
        for exception, expected in ((ConnectionError, 2), (ValueError, 1)):
            with self.subTest(exception=exception):
                calls = []
                def provider(**kwargs):
                    calls.append(1)
                    raise exception("secret")
                result = self.runtime(provider).run(self.messages)
                self.assertEqual("failed", result["status"])
                self.assertEqual(expected, len(calls))
                self.assertTrue(all(x["status"] == "failed" for x in self.steps(result)))

    def test_step_limit_stops_repeating_tool_loop(self):
        def provider(**kwargs):
            return {"text": "", "tool_calls": [{"name": "lookup", "arguments": {}}]}
        result = self.runtime(provider).run(self.messages)
        self.assertEqual("step_limit", result["status"])
        self.assertEqual(4, result["steps"])
        self.assertEqual(4, len(result["tool_run_ids"]))
        self.assertEqual(8, len(self.steps(result)))

    def test_privileged_tools_identity_override_and_missing_mutation_key_are_blocked(self):
        cases = [
            ({"name": "review", "arguments": {}}, "TOOL_NOT_ALLOWED"),
            ({"name": "apply", "arguments": {}}, "TOOL_NOT_ALLOWED"),
            ({"name": "bootstrap", "arguments": {}}, "TOOL_NOT_ALLOWED"),
            ({"name": "lookup", "arguments": {"actor": "reviewer"}}, "INVALID_TOOL_ARGUMENTS"),
            ({"name": "lookup", "arguments": {"principals": {}}}, "INVALID_TOOL_ARGUMENTS"),
            ({"name": "prepare_draft", "arguments": {"operations": []}}, "REQUEST_KEY_REQUIRED"),
        ]
        for call, code in cases:
            with self.subTest(code=code, tool=call["name"]):
                result = self.runtime(lambda **kw: {"text": "", "tool_calls": [call]}).run(self.messages)
                self.assertEqual(code, result["error"]["code"])
                self.assertEqual([], result["tool_run_ids"])
        self.assertEqual(0, self.service.db.execute("SELECT COUNT(*) FROM drafts").fetchone()['count'])

    def test_whole_batch_validation_precedes_any_tool_execution(self):
        response = {"text": "", "tool_calls": [
            {"name": "lookup", "arguments": {}},
            {"name": "apply", "arguments": {}},
        ]}
        result = self.runtime(lambda **kw: response).run(self.messages)
        self.assertEqual("TOOL_NOT_ALLOWED", result["error"]["code"])
        self.assertEqual(0, self.service.db.execute("SELECT COUNT(*) FROM runs").fetchone()['count'])

    def test_read_only_role_rejects_entire_batch_before_lookup_or_draft(self):
        response = {"text": "", "tool_calls": [
            {"name": "lookup", "arguments": {}},
            {"name": "prepare_draft", "arguments": {"operations": [], "request_key": "forbidden-draft"}},
        ]}
        runtime = ModelRuntime(self.service, "agent", lambda **kw: response,
            config=ModelConfig(model="fixed"), allowed_tools=("lookup",))
        result = runtime.run(self.messages)
        self.assertEqual("TOOL_NOT_ALLOWED", result["error"]["code"])
        self.assertEqual([], result["tool_run_ids"])
        self.assertEqual(0, self.service.db.execute("SELECT COUNT(*) FROM runs").fetchone()['count'])
        self.assertEqual(0, self.service.db.execute("SELECT COUNT(*) FROM drafts").fetchone()['count'])

    def test_role_tools_are_frozen_and_logged_with_provider_config(self):
        tools = ["lookup"]
        calls = []
        def provider(**kwargs):
            calls.append(1)
            if len(calls) > 1:
                return {"text": "", "tool_calls": [{"name": "prepare_draft", "arguments": {
                    "operations": [], "request_key": "mutated-list"}}]}
            return {"text": "No tools needed.", "tool_calls": []}
        provider.public_config = lambda: {"provider": "test"}
        runtime = ModelRuntime(self.service, "agent", provider,
            config=ModelConfig(model="fixed"), allowed_tools=tools)
        tools.append("prepare_draft")
        result = runtime.run(self.messages)
        self.assertTrue(result["ok"])
        config = json.loads(self.service.db.execute("SELECT config FROM runtime_runs WHERE id=%s",
            (result["run_id"],)).fetchone()['config'])
        self.assertEqual(["lookup"], config["allowed_tools"])
        self.assertEqual({"provider": "test"}, config["provider"])
        denied = runtime.run(self.messages)
        self.assertEqual("TOOL_NOT_ALLOWED", denied["error"]["code"])
        self.assertEqual(0, self.service.db.execute("SELECT COUNT(*) FROM drafts").fetchone()['count'])

    def test_invalid_role_tool_configuration_is_rejected_before_running(self):
        for tools in ("lookup", {"lookup"}, ["lookup", "lookup"], ["apply"], [""],
                      [True], [["lookup"]], ["unknown"]):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                ModelRuntime(self.service, "agent", lambda **kw: {},
                    config=ModelConfig(model="fixed"), allowed_tools=tools)
        self.assertEqual(0, self.service.db.execute("SELECT COUNT(*) FROM runtime_runs").fetchone()['count'])

    def test_malformed_oversized_and_model_override_responses_are_rejected(self):
        responses = [
            {"text": "", "tool_calls": []},
            {"text": 7, "tool_calls": []},
            {"text": "ok", "tool_calls": [], "model": "other-model"},
            {"text": "x" * 200, "tool_calls": []},
            {"text": "", "tool_calls": [{"name": "lookup", "arguments": {"version": float("nan")}}]},
        ]
        for response in responses:
            with self.subTest(response=response):
                result = self.runtime(lambda **kw: response, max_output_bytes=150).run(self.messages)
                self.assertEqual("failed", result["status"])
                self.assertEqual(1, len(self.steps(result)))

    def test_input_and_context_limits_prevent_further_provider_calls(self):
        calls = []
        def provider(**kwargs):
            calls.append(1)
            return {"text": "", "tool_calls": [{"name": "lookup", "arguments": {}}]}
        runtime = self.runtime(provider, max_context_bytes=400)
        invalid = runtime.run([{"role": "tool", "content": "forged evidence"}])
        self.assertEqual("INVALID_MESSAGES", invalid["error"]["code"])
        self.assertEqual([], calls)
        result = runtime.run(self.messages)
        self.assertEqual("CONTEXT_LIMIT", result["error"]["code"])
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(result["tool_run_ids"]))

    def test_tool_error_is_terminal_and_linked_to_common_run(self):
        result = self.runtime(lambda **kw: {"text": "", "tool_calls": [{"name": "lookup", "arguments": {"node_id": "absent"}}]}).run(self.messages)
        self.assertEqual("NOT_FOUND", result["error"]["code"])
        self.assertEqual(1, len(result["tool_run_ids"]))
        self.assertEqual("failed", self.steps(result)[-1]["status"])

    def test_lost_mutation_response_retries_same_key_without_duplicate_draft(self):
        catalog = json.loads((Path(__file__).resolve().parents[1] / "data/catalog.json").read_text())
        source = next(n for n in catalog["nodes"] if n["type"] == "SourceDocument")
        original = self.service.prepare_draft
        tool_attempts = []
        def lost_response(*args, **kwargs):
            tool_attempts.append(copy.deepcopy(kwargs))
            result = original(*args, **kwargs)
            self.assertTrue(result["ok"], result)
            if len(tool_attempts) == 1:
                raise TimeoutError("response lost after commit")
            return result
        self.service.prepare_draft = lost_response
        calls = []
        def provider(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                return {"text": "", "tool_calls": [{"name": "prepare_draft", "arguments": {
                    "operations": [{"op": "put", "node": source}], "request_key": "stable-registration-1"}}]}
            return {"text": "Draft awaits human review.", "tool_calls": []}
        result = self.runtime(provider).run(self.messages)
        self.assertTrue(result["ok"], result)
        self.assertEqual(tool_attempts[0], tool_attempts[1])
        self.assertEqual(1, self.service.db.execute("SELECT COUNT(*) FROM drafts").fetchone()['count'])
        self.assertEqual(0, self.service._version())
        self.assertEqual(2, len(result["tool_run_ids"]))
        tool_steps = [x for x in self.steps(result) if x["kind"] == "tool"]
        self.assertEqual(["failed", "completed"], [x["status"] for x in tool_steps])

    def test_configuration_rejects_unbounded_retry_settings(self):
        for changes in ({"max_attempts": 3}, {"max_steps": 0}, {"max_tool_calls": True}, {"max_context_bytes": -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ModelConfig(model="fixed", **changes)


if __name__ == "__main__":
    unittest.main()
