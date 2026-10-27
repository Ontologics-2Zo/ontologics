"""B runtime regression checks; PostgreSQL cases remain separate from offline checks."""
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from psycopg.pq import TransactionStatus

from tests.db import database_for_test
from ontologics.runtime import ModelConfig, ModelRuntime as CommonRuntime, _Failure
from ontologics.service import AgentGateway as CommonGateway
from ontologics.order_fulfillment.runtime import ModelRuntime
from ontologics.order_fulfillment.service import AgentGateway, Service


class RuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.service = SimpleNamespace(db=SimpleNamespace(
            info=SimpleNamespace(transaction_status=TransactionStatus.IDLE)))
        self.config = ModelConfig(model="scripted-runtime-boundary")
        self.provider = lambda **kw: {"text": "Done.", "tool_calls": []}

    def runtime(self, allowed_tools=None):
        return ModelRuntime(self.service, "agent", self.provider,
                            config=self.config, allowed_tools=allowed_tools)

    def test_invalid_scopes_fail_before_database_or_provider_use(self):
        # No service/database object is needed when the scope itself is invalid.
        for tools in ("lookup", [], (), set(), frozenset(), ["lookup", "lookup"],
                      ["apply"], ["review"], [""], [True], [["lookup"]], ["unknown"]):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                ModelRuntime(None, "agent", self.provider, config=self.config,
                             allowed_tools=tools)

    def test_supported_collections_are_frozen_per_instance(self):
        for collection in (list, tuple, set, frozenset):
            with self.subTest(collection=collection.__name__):
                tools = collection(["delivery_round"])
                runtime = self.runtime(tools)
                if isinstance(tools, list):
                    tools.append("prepare_draft")
                elif isinstance(tools, set):
                    tools.add("prepare_draft")
                self.assertEqual(frozenset({"delivery_round"}), runtime._gateway.allowed_tools)
                self.assertEqual(frozenset({"lookup"}), self.runtime(["lookup"])._gateway.allowed_tools)

    def test_default_b_scope_and_execution_loop_stay_separate_from_common(self):
        self.assertEqual(AgentGateway.ALLOWED, self.runtime()._gateway.allowed_tools)
        common = CommonRuntime(self.service, "agent", self.provider, config=self.config)
        self.assertEqual(CommonGateway.ALLOWED, common._gateway.allowed_tools)
        self.assertNotIn("delivery_round", common._gateway.allowed_tools)
        self.assertIs(ModelRuntime.run, CommonRuntime.run)
        self.assertIs(ModelRuntime._attempt, CommonRuntime._attempt)

    def test_entire_batch_is_rejected_without_invoking_any_tool(self):
        runtime = self.runtime(["lookup"])
        response = {"text": "", "tool_calls": [
            {"name": "lookup", "arguments": {}},
            {"name": "prepare_draft", "arguments": {
                "operations": [], "request_key": "forbidden"}},
        ]}
        with patch.object(runtime._gateway, "call") as call:
            with self.assertRaises(_Failure) as caught:
                runtime._model_response(response)
            call.assert_not_called()
        self.assertEqual({"code": "TOOL_NOT_ALLOWED",
                          "message": "Tool is not exposed to this Agent role"}, caught.exception.error)

    def test_b_tool_response_and_identity_argument_checks_are_inherited(self):
        runtime = self.runtime(["delivery_round"])
        response = {"text": "", "tool_calls": [{"name": "delivery_round",
                    "arguments": {"delivery_round_id": "round:1", "version": 1}}]}
        self.assertEqual(response, runtime._model_response(response))
        for arguments in ({"actor": "someone-else"}, {"principals": {}}, []):
            with self.subTest(arguments=arguments), self.assertRaises(_Failure) as caught:
                runtime._model_response({"text": "", "tool_calls": [
                    {"name": "delivery_round", "arguments": arguments}]})
            self.assertEqual("INVALID_TOOL_ARGUMENTS", caught.exception.error["code"])
        with self.assertRaises(_Failure) as caught:
            runtime._model_response({"text": "", "tool_calls": []})
        self.assertEqual("EMPTY_MODEL_RESPONSE", caught.exception.error["code"])

    def test_parent_configuration_and_transaction_guards_remain_active(self):
        for provider, config in ((None, self.config), (self.provider, None)):
            with self.subTest(provider=provider, config=config), self.assertRaises(ValueError):
                ModelRuntime(self.service, "agent", provider, config=config)
        self.service.db.info.transaction_status = TransactionStatus.INTRANS
        with self.assertRaisesRegex(ValueError, "inside a transaction"):
            self.runtime(["delivery_round"])


class RuntimeDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(database_for_test(self), principals={
            "agent": {"kind": "agent", "roles": ["read", "draft"]},
            "reviewer": {"kind": "human", "roles": ["read", "review", "apply"]},
        })
        self.addCleanup(self.service.close)
        self.messages = [{"role": "system", "content": "Use B tools."},
                         {"role": "user", "content": "private request 937"}]

    def test_default_b_tools_are_logged(self):
        runtime = ModelRuntime(self.service, "agent", lambda **kw: {
            "text": "No tools needed.", "tool_calls": []}, config=ModelConfig(model="fixed"))
        result = runtime.run(self.messages)
        self.assertTrue(result["ok"])
        row = self.service.db.execute("SELECT config FROM runtime_runs WHERE id=%s",
                                     (result["run_id"],)).fetchone()
        self.assertEqual(sorted(AgentGateway.ALLOWED), json.loads(row["config"])["allowed_tools"])

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
        for tools in ("lookup", [], (), set(), frozenset(), ["lookup", "lookup"], ["apply"], [""],
                      [True], [["lookup"]], ["unknown"]):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                ModelRuntime(self.service, "agent", lambda **kw: {},
                    config=ModelConfig(model="fixed"), allowed_tools=tools)
        self.assertEqual(0, self.service.db.execute("SELECT COUNT(*) FROM runtime_runs").fetchone()['count'])

    def test_host_tool_collections_keep_gateway_scope_and_output_hash(self):
        for collection in (list, tuple, set, frozenset):
            with self.subTest(collection=collection.__name__):
                calls = []
                def provider(**kwargs):
                    calls.append(1)
                    if len(calls) == 1:
                        return {"text": "", "tool_calls": [{"name": "lookup",
                            "arguments": {"kind": "ProductModel"}}]}
                    return {"text": "Read-only result.", "tool_calls": []}
                runtime = ModelRuntime(self.service, "agent", provider,
                    config=ModelConfig(model="fixed"), allowed_tools=collection(["lookup"]))
                result = runtime.run(self.messages)
                self.assertTrue(result["ok"])
                expected_hash = hashlib.sha256(json.dumps(result["text"], ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                self.assertEqual(expected_hash, result["output_hash"])
                row = self.service.db.execute("SELECT config,output_hash FROM runtime_runs WHERE id=%s",
                    (result["run_id"],)).fetchone()
                self.assertEqual(["lookup"], json.loads(row["config"])["allowed_tools"])
                self.assertEqual(expected_hash, row["output_hash"])
                denied = runtime._gateway.call("prepare_draft", {"operations": [], "request_key": "denied"})
                self.assertEqual("TOOL_NOT_ALLOWED", denied["errors"][0]["code"])
        self.assertEqual(0, self.service.db.execute("SELECT COUNT(*) FROM drafts").fetchone()['count'])
