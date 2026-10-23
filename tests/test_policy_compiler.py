"""Offline policy and real-SDK transport checks; never contact OpenRouter."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from policy_compiler import CompilationError, PolicyValidationError, compile_policy, validate_policy
from policy_compiler.validator import load_json, validate_context


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "policy_compiler" / "examples"
REQUEST = "Send Alice the Project X update on Slack"


def response(policy, finish="stop"):
    text = policy if isinstance(policy, str) else json.dumps(policy)
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason=finish)])


class PolicyCompilerTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((EXAMPLES / "context.json").read_text())
        self.policy = json.loads((EXAMPLES / "policy.json").read_text())

    def compile(self, policy=None, request=REQUEST, completion=None):
        return compile_policy(request, **self.context,
                              completion=completion or (lambda *a, **kw: response(policy or self.policy)))

    def test_slack_request_has_explicit_action_and_inferred_directory_read(self):
        policy = self.compile()
        self.assertEqual(policy.status, "ready")
        self.assertEqual([a.tool for a in policy.actions], ["slack"])
        self.assertEqual(policy.actions[0].recipients[0].recipient_id, "alice")
        self.assertEqual(policy.reads[1].evidence.basis, "necessary_inference")

    def test_multiple_legitimate_reads_are_retained(self):
        self.policy["reads"].append(dict(
            resource_id="project_x_timeline", evidence=dict(
                basis="necessary_inference", request_excerpt="Project X update",
                rationale="Use the timeline to interpret the current update.")))
        policy = self.compile()
        self.assertEqual({r.resource_id for r in policy.reads}, {
            "project_x_update", "project_x_timeline", "coworker_directory"})

    def test_ambiguous_recipient_returns_questions_and_no_permissions(self):
        self.context["trusted_resource_metadata"]["recipients"].append(dict(
            recipient_id="alice_2", name="Alice Wong", aliases=["Alice"]))
        policy = self.nonready("clarification_required")
        policy["ambiguities"] = [dict(field="recipient", question="Which Alice do you mean?",
                                      candidate_ids=["alice", "alice_2"])]
        result = self.compile(policy)
        self.assertEqual(result.status, "clarification_required")
        self.assertFalse(result.reads or result.actions)
        result = self.compile()  # A guessed identity is downgraded, never accepted.
        self.assertEqual(result.status, "clarification_required")
        self.assertFalse(result.reads or result.actions)
        self.assertEqual(result.ambiguities[0].candidate_ids, ["alice", "alice_2"])
        with self.assertRaisesRegex(PolicyValidationError, "ambiguous"):
            validate_policy(self.policy, REQUEST, self.context)

    def test_recipient_substring_is_not_an_identity_reference(self):
        self.context["trusted_resource_metadata"]["recipients"][0]["aliases"].append("Ali")
        self.policy["actions"][0]["recipients"][0]["request_reference"] = "Ali"
        with self.assertRaisesRegex(PolicyValidationError, "not present"):
            self.compile()

    def test_available_but_unrelated_email_is_not_authorized(self):
        self.policy["actions"][0]["tool"] = "email"
        self.policy["actions"][0]["operation"] = "send"
        with self.assertRaisesRegex(PolicyValidationError, "ceiling"):
            self.compile()

    def test_overprivileged_request_can_return_denial_but_never_salary_access(self):
        request = "Read the salary data"
        policy = self.nonready("denied", request)
        policy["denial_reason"] = "Salary data is outside the caller's permissions."
        result = self.compile(policy, request)
        self.assertEqual(result.status, "denied")
        self.assertFalse(result.reads or result.actions)
        policy.update(status="ready", denial_reason=None, reads=[dict(
            resource_id="salary_data", evidence=dict(basis="explicit",
                request_excerpt=request, rationale="The user requested this data."))])
        with self.assertRaisesRegex(PolicyValidationError, "ceiling"):
            self.compile(policy, request)

    def nonready(self, status, request=REQUEST):
        policy = deepcopy(self.policy)
        policy.update(original_request=request, status=status, reads=[], actions=[])
        return policy

    def test_global_and_user_ceilings_are_checked_independently(self):
        for ceiling in ("existing_user_permissions", "global_rules"):
            with self.subTest(ceiling=ceiling):
                context = deepcopy(self.context)
                context[ceiling]["operations"] = []
                with self.assertRaisesRegex(PolicyValidationError, "ceiling"):
                    validate_policy(self.policy, REQUEST, context)
                context = deepcopy(self.context)
                context[ceiling]["read_resource_ids"] = ["coworker_directory"]
                with self.assertRaisesRegex(PolicyValidationError, "ceiling"):
                    validate_policy(self.policy, REQUEST, context)

    def test_cannot_remove_or_widen_argument_constraints(self):
        for constraints in ({}, {"format": ["markdown"]}, {"format": ["plain", "markdown"]}):
            with self.subTest(constraints=constraints):
                self.policy["actions"][0]["argument_constraints"] = constraints
                with self.assertRaisesRegex(PolicyValidationError, "restriction"):
                    self.compile()

    def test_wrong_argument_types_unknown_arguments_and_wildcards_fail(self):
        for constraints in ({"format": [True]}, {"unknown": ["plain"]}):
            with self.subTest(constraints=constraints):
                self.policy["actions"][0]["argument_constraints"] = constraints
                with self.assertRaises(PolicyValidationError):
                    self.compile()
        self.policy["reads"][0]["resource_id"] = "*"
        with self.assertRaises(PolicyValidationError):
            self.compile()

    def test_bool_is_not_integer_constraint(self):
        tool = self.context["available_tool_schemas"][0]
        tool["arguments"]["urgent"] = {"value_type": "boolean", "enum": [True, False]}
        for ceiling in ("existing_user_permissions", "global_rules"):
            self.context[ceiling]["operations"][0]["argument_constraints"]["urgent"] = [False]
        self.policy["actions"][0]["argument_constraints"]["urgent"] = [0]
        with self.assertRaisesRegex(PolicyValidationError, "type"):
            self.compile()

    def test_operation_counts_and_aggregate_counts_cannot_expand(self):
        self.policy["actions"][0]["max_calls"] = 2
        with self.assertRaisesRegex(PolicyValidationError, "count"):
            self.compile()
        # Individually valid operations must still obey the total task budget.
        self.policy["actions"][0]["max_calls"] = 1
        tool = deepcopy(self.context["available_tool_schemas"][0])
        tool["operation"] = "send_followup"
        self.context["available_tool_schemas"].append(tool)
        for ceiling in ("existing_user_permissions", "global_rules"):
            limit = deepcopy(self.context[ceiling]["operations"][0])
            limit["operation"] = "send_followup"
            self.context[ceiling]["operations"].append(limit)
        action = deepcopy(self.policy["actions"][0])
        action["operation"] = "send_followup"
        self.policy["actions"].append(action)
        with self.assertRaisesRegex(PolicyValidationError, "aggregate"):
            self.compile()

    def test_action_resource_recipient_and_expiration_cannot_expand(self):
        cases = [
            lambda p: p["actions"][0].update(resource_ids=["salary_data"]),
            lambda p: p["actions"][0].update(recipients=[]),
            lambda p: p["expiration"].update(max_duration_seconds=3601),
            lambda p: p["expiration"].update(condition="never"),
            lambda p: p["actions"][0].update(max_calls=0),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                policy = deepcopy(self.policy)
                mutate(policy)
                with self.assertRaises(PolicyValidationError):
                    self.compile(policy)
        request = "Send Bob the Project X update on Slack"
        policy = deepcopy(self.policy)
        policy.update(original_request=request)
        policy["actions"][0]["recipients"] = [dict(recipient_id="bob", request_reference="Bob")]
        policy["actions"][0]["evidence"]["request_excerpt"] = request
        policy["reads"][1]["evidence"]["request_excerpt"] = "Send Bob"
        with self.assertRaisesRegex(PolicyValidationError, "recipient scope"):
            self.compile(policy, request)

    def test_duplicate_operations_cannot_multiply_budget(self):
        self.policy["actions"].append(deepcopy(self.policy["actions"][0]))
        with self.assertRaisesRegex(PolicyValidationError, "Duplicate"):
            self.compile()

    def test_uncertain_or_fabricated_evidence_cannot_grant_permissions(self):
        for changes in ({"basis": "uncertain"}, {"request_excerpt": "Delete the database"}):
            with self.subTest(changes=changes):
                policy = deepcopy(self.policy)
                policy["reads"][0]["evidence"].update(changes)
                with self.assertRaises(PolicyValidationError):
                    self.compile(policy)

    def test_malformed_json_types_extra_fields_and_request_rewriting_fail(self):
        cases = ["```json\n{}\n```", "{}{}", '{"x": 1, "x": 2}', '{"x": NaN}', '[]']
        for text in cases:
            with self.subTest(text=text), self.assertRaises(PolicyValidationError):
                self.compile(completion=lambda *a, **kw: response(text))
        for update in ({"surprise": "field"}, {"original_request": "Different task"}):
            with self.subTest(update=update), self.assertRaises(PolicyValidationError):
                self.compile(self.policy | update)
        for value in (True, "1", -1):
            policy = deepcopy(self.policy)
            policy["actions"][0]["max_calls"] = value
            with self.subTest(value=value), self.assertRaises(PolicyValidationError):
                self.compile(policy)

    def test_no_grants_on_clarification_or_denial(self):
        for status in ("clarification_required", "denied"):
            with self.subTest(status=status), self.assertRaises(PolicyValidationError):
                self.compile(self.policy | {"status": status})
        with self.assertRaises(PolicyValidationError):
            self.compile(self.nonready("denied"))
        with self.assertRaises(PolicyValidationError):
            self.compile(self.nonready("clarification_required"))

    def test_self_contained_task_can_require_no_external_permissions(self):
        request = "What is two plus two?"
        policy = self.compile(self.nonready("ready", request), request)
        self.assertEqual(policy.status, "ready")
        self.assertFalse(policy.reads or policy.actions)

    def test_bad_context_is_rejected_before_any_llm_call(self):
        self.context["global_rules"]["read_resource_ids"].append("invented")
        calls = []
        with self.assertRaises(PolicyValidationError):
            self.compile(completion=lambda *a, **kw: calls.append(kw))
        self.assertEqual(calls, [])
        with self.assertRaises(PolicyValidationError):
            compile_policy(" ", **self.context, completion=lambda *a, **kw: calls.append(kw))
        self.assertEqual(calls, [])

    def test_mutated_context_model_is_revalidated(self):
        context = validate_context(self.context)
        context.global_rules.read_resource_ids.append("invented")
        with self.assertRaises(PolicyValidationError):
            validate_policy(self.policy, REQUEST, context)

    def test_truncated_valid_json_and_provider_errors_are_rejected_without_secrets(self):
        for finish in ("length", "content_filter", None):
            with self.subTest(finish=finish), self.assertRaises(CompilationError):
                self.compile(completion=lambda *a, **kw: response(self.policy, finish))
        def broken(*a, **kw):
            raise RuntimeError("Authorization: Bearer SECRET-TEST-VALUE")
        with self.assertRaises(CompilationError) as caught:
            self.compile(completion=broken)
        self.assertNotIn("SECRET-TEST-VALUE", str(caught.exception))
        with self.assertRaises(PolicyValidationError) as caught:
            self.compile(self.policy | {"SECRET-TEST-VALUE": "extra"})
        self.assertNotIn("SECRET-TEST-VALUE", str(caught.exception))

    def test_real_sdk_transport_uses_configured_model_json_mode_and_existing_helper(self):
        import openrouter_client as backend
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "offline", "object": "chat.completion", "created": 0,
                "model": "test/policy-model", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": json.dumps(self.policy)},
                    "finish_reason": "stop"}],
            })
        client = OpenAI(base_url=backend.OPENROUTER_BASE_URL, api_key="offline-test-key",
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with client, patch.object(backend, "get_client", return_value=client), patch.dict(
                os.environ, {"OPENROUTER_MODEL": "test/policy-model"}):
            result = compile_policy(REQUEST, **self.context)
        self.assertEqual(result.status, "ready")
        self.assertEqual(len(requests), 1)
        payload = requests[0]
        self.assertEqual(payload["model"], "test/policy-model")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["temperature"], 0)
        supplied = json.loads(payload["messages"][1]["content"])
        self.assertEqual(supplied, {"original_request": REQUEST, **self.context})
        self.assertNotIn("offline-test-key", json.dumps(payload))

    def test_cli_offline_demo_and_request_binding(self):
        command = [sys.executable, "-m", "policy_compiler", "--request", REQUEST,
                   "--context", str(EXAMPLES / "context.json"),
                   "--validate-response", str(EXAMPLES / "policy.json")]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.policy)
        command[4] = "A different request"
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("original authenticated request", result.stderr)

    def test_imports_do_not_load_environment_or_heavy_models(self):
        code = "import sys; import policy_compiler; assert not any(n in sys.modules for n in ('openrouter_client', 'torch', 'transformers', 'autogen'))"
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
