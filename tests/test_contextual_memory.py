"""Memory authorization and genuine A-MemGuard component integration, without network."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import inspect
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI
from openai.types.chat import ChatCompletion

from contextual_memory.checker import build_checker, checker_class
from contextual_memory.experiment import load_fixture, replay, run_experiment, score
from contextual_memory.gate import PolicyMemoryGate
from contextual_memory.pipeline import LocalMemoryStore, MemoryMetadata, MemoryPipeline
from contextual_memory.prepare import DEFAULT_FIXTURE, DEFAULT_POLICIES
from contextual_memory.transport import RecordedCompletion, TransportError
from policy_compiler import PolicyValidationError
from policy_compiler.evaluate import oracle_policy


def response(text, finish="stop"):
    return ChatCompletion.model_validate(dict(id="offline", object="chat.completion", created=0,
        model="test/memory", choices=[dict(index=0, finish_reason=finish,
            message=dict(role="assistant", content=text))],
        usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15)))


def pass_all(messages):
    """An intentionally permissive mock judge, with no corpus/annotation access."""
    text = messages[-1]["content"]
    if "**Reasoning Chains to Audit:**" in text:
        section = text.split("**Reasoning Chains to Audit:**", 1)[1].strip().split("\n\n", 1)[0]
        ids = [int(i) for i in re.findall(r"(?:^|\n)(\d+):", section)]
        return response(json.dumps([dict(id=i, consistent=True, safe=True) for i in ids]))
    return response("Project -> reports -> status")


class MemoryGateTests(unittest.TestCase):
    def setUp(self):
        self.context, self.suite, self.store, self.labels = load_fixture(DEFAULT_FIXTURE)
        self.case = self.suite.cases[0]
        self.policy = oracle_policy(self.case, self.context)

    def test_fixture_separates_authorization_relevance_and_poisoning(self):
        self.assertEqual(len(self.suite.cases), 20)
        self.assertEqual(len(self.store.metadata), 20)
        gate = PolicyMemoryGate(self.policy, self.case.request, self.context)
        allowed = gate.filter(self.store.metadata)
        self.assertTrue(any(self.labels["memory_labels"][m.memory_id]["poisoned"] for m in allowed))
        denied = set(m.memory_id for m in self.store.metadata) - set(m.memory_id for m in allowed)
        self.assertTrue(any(not self.labels["memory_labels"][mid]["poisoned"] for mid in denied))
        self.assertTrue(all(m.resource_id == "orion_brief" for m in allowed))
        self.assertTrue(all("poisoned" not in m.model_dump() for m in self.store.metadata))

    def test_gate_filters_before_payload_read_or_checker_exposure(self):
        seen = []
        def completion(messages):
            seen.append(messages)
            return pass_all(messages)
        gate = PolicyMemoryGate(self.policy, self.case.request, self.context)
        result = MemoryPipeline(self.store, build_checker(completion)).run(self.case.request, gate=gate)
        allowed = {m.memory_id for m in self.store.metadata if m.resource_id == "orion_brief"}
        self.assertEqual(set(self.store.accesses), allowed)
        self.assertEqual(set(result["trace"]["checker_input_ids"]), allowed)
        self.assertNotIn("compensation ledger", json.dumps(seen).casefold())
        self.assertNotIn("memory_labels", json.dumps(seen))

    def test_poison_cannot_grant_itself_resource_authority(self):
        prohibited = next(m for m in self.store.metadata if m.resource_id == "acquisition_plan")
        gate = PolicyMemoryGate(self.policy, self.case.request, self.context)
        with patch.object(self.store, "read", wraps=self.store.read) as read:
            MemoryPipeline(self.store, build_checker(pass_all)).run(self.case.request, gate=gate)
            self.assertNotIn(prohibited.memory_id, [c.args[0] for c in read.call_args_list])

    def test_nonready_policies_do_not_read_or_call_checker(self):
        for case in [self.suite.cases[11], self.suite.cases[15]]:
            gate = PolicyMemoryGate(oracle_policy(case, self.context), case.request, self.context)
            checker = build_checker(lambda _: self.fail("No model call allowed"))
            result = MemoryPipeline(self.store, checker).run(case.request, gate=gate)
            self.assertEqual(result["context"], "")
            self.assertEqual(self.store.accesses, [])

    def test_invalid_and_wrong_request_policies_do_not_construct_gate(self):
        with self.assertRaises(PolicyValidationError):
            PolicyMemoryGate(self.policy, "Different request", self.context)
        broken = deepcopy(self.policy)
        broken["reads"][0]["resource_id"] = "compensation_ledger"
        with self.assertRaises(PolicyValidationError):
            PolicyMemoryGate(broken, self.case.request, self.context)

    def test_unknown_metadata_and_expired_or_completed_tasks_are_denied(self):
        clock = [0]
        gate = PolicyMemoryGate(self.policy, self.case.request, self.context, clock=lambda: clock[0])
        unknown = MemoryMetadata(memory_id="new", resource_id="unknown", title="Orion release brief")
        self.assertEqual(gate.filter([unknown]), ())
        self.assertTrue(gate.filter(self.store.metadata))
        clock[0] = self.policy["expiration"]["max_duration_seconds"]
        self.assertFalse(gate.filter(self.store.metadata))
        clock[0] = 0
        gate.complete()
        self.assertFalse(gate.filter(self.store.metadata))

    def test_gate_snapshots_grants_against_caller_mutation(self):
        gate = PolicyMemoryGate(self.policy, self.case.request, self.context)
        self.policy["reads"].clear()
        self.assertTrue(gate.filter(self.store.metadata))

    def test_ungated_mode_is_explicit_and_shared_top_k_is_bounded(self):
        result = MemoryPipeline(self.store, build_checker(pass_all), k=4).run(self.case.request)
        self.assertEqual(len(result["trace"]["eligible_ids"]), 20)
        self.assertEqual(len(result["trace"]["checker_input_ids"]), 4)
        self.assertEqual(len(result["trace"]["delivered_ids"]), 4)
        with self.assertRaises(ValueError):
            MemoryPipeline(self.store, build_checker(pass_all), k=0).run(self.case.request)

    def test_permitted_poison_survives_an_unsafe_judge_not_the_gate(self):
        gate = PolicyMemoryGate(self.policy, self.case.request, self.context)
        result = MemoryPipeline(self.store, build_checker(pass_all)).run(self.case.request, gate=gate)
        self.assertTrue(any(self.labels["memory_labels"][mid]["poisoned"] for mid in result["trace"]["delivered_ids"]))

    def test_bad_judgment_is_failure_with_observed_upstream_exposure(self):
        def bad(messages):
            if "**Reasoning Chains to Audit:**" in messages[-1]["content"]:
                return response('[{"id":0,"consistent":"false","safe":true}]')
            return response("A -> B")
        trace = {}
        with self.assertRaises(ValueError):
            MemoryPipeline(self.store, build_checker(bad)).run(self.case.request, trace=trace)
        self.assertEqual(len(trace["checker_input_ids"]), 4)
        self.assertFalse(trace["checker_completed"])
        self.assertEqual(trace["delivered_ids"], [])

    def test_checker_adapter_restores_import_bindings_and_uses_existing_source(self):
        old_path = list(sys.path)
        old_env = sys.modules.get("llm_env")
        checker_class.cache_clear()
        cls = checker_class()
        self.assertEqual(Path(inspect.getsourcefile(cls.check)).resolve(),
                         Path(__file__).resolve().parents[1] / "EhrAgent/ehragent/consistency.py")
        self.assertEqual(sys.path, old_path)
        self.assertIs(sys.modules.get("llm_env"), old_env)

    def test_unknown_payload_and_unsafe_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.read("../secret")
        with self.assertRaises(ValueError):
            MemoryMetadata(memory_id="../secret", resource_id="orion_brief", title="Title")


class MemoryExperimentTests(unittest.TestCase):
    def test_all_four_arms_and_offline_replay_use_real_checker_without_network(self):
        import openrouter_client as backend
        calls = []
        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            self.assertNotIn("memory_labels", json.dumps(body))
            self.assertNotIn("required_clean_ids", json.dumps(body))
            self.assertLessEqual(body["max_tokens"], 1024)
            return httpx.Response(200, json=pass_all(body["messages"]).model_dump())
        client = OpenAI(base_url=backend.OPENROUTER_BASE_URL, api_key="offline-private-test-value",
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as temp, client, patch.object(
                backend, "get_client", return_value=client), redirect_stdout(io.StringIO()):
            run = run_experiment(DEFAULT_FIXTURE, DEFAULT_POLICIES, Path(temp), model="test/memory")
            records = json.loads((run / "records.json").read_text())
            metrics = json.loads((run / "metrics.json").read_text())
            self.assertEqual(len(records), 80)
            self.assertEqual(metrics["revised_policy"]["outcomes"]["policy_error"], 2)
            self.assertEqual(metrics["keyword_policy"]["outcomes"]["clarification_required"], 4)
            for name in ("manual_policy", "revised_policy"):
                self.assertEqual(metrics[name]["counts"]["prohibited_checker_input_cases"], 0)
                self.assertGreater(metrics[name]["counts"]["permitted_poison_checker_input_cases"], 0)
            self.assertTrue(any(r["cache_hits"] for r in records))
            self.assertLess(len(calls), 400)
            self.assertNotIn("offline-private-test-value", (run / "api.jsonl").read_text())
            with patch.object(backend, "get_client", side_effect=AssertionError("No network in replay")):
                replay(run)
            context, suite, _, labels = load_fixture(run / "fixture")
            with self.assertRaises(ValueError):
                score(records[:-1], suite, labels)
            (run / "metrics.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                replay(run)

    def test_transport_no_retries_failure_circuit_and_sanitized_errors(self):
        import openrouter_client as backend
        attempts = []
        def handler(request):
            attempts.append(request)
            return httpx.Response(503, json={"error": {"message": "PRIVATE-PROVIDER-ERROR"}})
        client = OpenAI(base_url=backend.OPENROUTER_BASE_URL, api_key="PRIVATE-KEY",
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as temp, client, patch.object(backend, "get_client", return_value=client):
            completion = RecordedCompletion(temp, "test/memory")
            for _ in range(3):
                with self.assertRaises(TransportError):
                    completion([dict(role="user", content="Hello")])
            self.assertEqual(len(attempts), 2)
            self.assertNotIn("PRIVATE", (Path(temp) / "api.jsonl").read_text())

    def test_transport_rejects_truncation_without_caching_it(self):
        import openrouter_client as backend
        def handler(_):
            return httpx.Response(200, json=response("chain", finish="length").model_dump())
        client = OpenAI(base_url=backend.OPENROUTER_BASE_URL, api_key="offline",
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as temp, client, patch.object(backend, "get_client", return_value=client):
            completion = RecordedCompletion(temp, "test/memory")
            with self.assertRaises(TransportError):
                completion([dict(role="user", content="Hello")])
            self.assertEqual(completion.cache, {})


if __name__ == "__main__":
    unittest.main()
