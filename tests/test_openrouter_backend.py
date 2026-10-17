"""Offline migration checks: real SDK requests, no key/network/model downloads."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

import openrouter_client as backend

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(text, prompt_tokens=10, completion_tokens=5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class OpenRouterBackendTests(unittest.TestCase):
    def test_sdk_request_uses_configured_model_messages_and_stop(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={
                "id": "offline", "object": "chat.completion", "created": 0,
                "model": "test/model", "choices": [{
                    "index": 0, "message": {"role": "assistant", "content": "generated text"},
                    "finish_reason": "stop",
                }],
            })

        client = OpenAI(
            base_url=backend.OPENROUTER_BASE_URL, api_key="test-key",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with client, patch.object(backend, "get_client", return_value=client), patch.dict(
            os.environ, {"OPENROUTER_MODEL": "test/model"}
        ):
            self.assertEqual(backend.call_llm("prompt", system="system", stop=["END"], max_tokens=32), "generated text")
        self.assertEqual(str(requests[0].url), "https://openrouter.ai/api/v1/chat/completions")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["model"], "test/model")
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": "system"}, {"role": "user", "content": "prompt"}
        ])
        self.assertEqual(payload["stop"], ["END"])
        self.assertEqual(payload["max_tokens"], 32)
        self.assertNotIn("logprobs", payload)

    def test_explicit_model_override_and_missing_key(self):
        with patch.dict(os.environ, {"OPENROUTER_MODEL": "env/model"}):
            self.assertEqual(backend.get_model("explicit/model"), "explicit/model")
        backend.get_client.cache_clear()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                backend.get_client()
        backend.get_client.cache_clear()

    def test_configurable_reasoning_inclusive_token_budget(self):
        with patch.dict(os.environ, {"OPENROUTER_MAX_TOKENS": "8192"}):
            self.assertEqual(backend.get_max_tokens(), 8192)
        for invalid in ("0", "-1", "many"):
            with patch.dict(os.environ, {"OPENROUTER_MAX_TOKENS": invalid}):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    backend.get_max_tokens()

    def test_empty_response_and_api_errors_are_not_text(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response(None))))
        with patch.object(backend, "get_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "no text"):
                backend.call_llm("prompt")
        with patch.object(backend, "get_client", side_effect=RuntimeError("API unavailable")):
            with self.assertRaisesRegex(RuntimeError, "API unavailable"):
                backend.call_llm("prompt")

    def test_api_module_imports_never_import_torch_or_transformers(self):
        code = """
import importlib.util, sys
from pathlib import Path
root = Path.cwd()
for directory in ('ReAct', 'EhrAgent/ehragent'):
    for filename in ('auditor.py', 'consistency.py'):
        spec = importlib.util.spec_from_file_location('check_module', root / directory / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
assert 'torch' not in sys.modules
assert 'transformers' not in sys.modules
assert 'sentence_transformers' not in sys.modules
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_consistency_checkers_preserve_indexes_and_real_usage(self):
        for directory in ("ReAct", "EhrAgent/ehragent"):
            module = load_module("consistency_test", f"{directory}/consistency.py")

            def generate(messages, **kwargs):
                prompt = messages[-1]["content"]
                if "Reasoning Chains to Audit" in prompt:
                    return response('[{"id":0,"consistent":true,"safe":true},{"id":1,"consistent":false,"safe":false}]')
                return response("Paris -> contains -> Louvre")

            with patch.object(module, "chat_completion", side_effect=generate):
                result = module.check_consistency("Paris?", ["good", "bad"], [12, 34], "example", knn=False)
            self.assertEqual([m["index"] for m in result["consistent_memories"]], [12])
            self.assertEqual([m["index"] for m in result["inconsistent_memories"]], [34])
            self.assertEqual(result["token_usage"], {"input_tokens": 30, "output_tokens": 15})
            with patch.object(module, "chat_completion", side_effect=RuntimeError("API unavailable")):
                with self.assertRaisesRegex(RuntimeError, "API unavailable"):
                    module.OpenRouterModel().generate_batch(["prompt"])

    def test_both_auditors_use_api_text(self):
        for directory in ("ReAct", "EhrAgent/ehragent"):
            module = load_module("auditor_test", f"{directory}/auditor.py")
            with patch.object(module, "chat_completion", side_effect=[
                response("[STATUS]: harmful"), response("[Santized Memory Log]: clean memory"),
            ]) as generate:
                self.assertEqual(module.audit_and_sanitize_item("test", "injected memory"), "clean memory")
                self.assertEqual(generate.call_count, 2)

    @unittest.skipUnless(importlib.util.find_spec("autogen"), "EHRAgent optional dependencies not installed")
    def test_ehragent_api_backbones_return_text_and_never_load_llama(self):
        import torch
        sys.path.insert(0, str(ROOT / "EhrAgent/ehragent"))
        try:
            module = load_module("medagent_test", "EhrAgent/ehragent/medagent.py")
            encoder = SimpleNamespace(to=lambda device: encoder, eval=lambda: None)
            memory = [{"question": "question", "knowledge": "knowledge", "code": "code"}]
            for backbone_name in ("openrouter", "llama3"):
                with patch.object(module, "load_models", return_value=(encoder, None, None)), patch.object(
                    module, "load_db_ehr", return_value=(torch.ones(1, 2), torch.ones(1, 2), memory)
                ), patch("transformers.AutoModelForCausalLM.from_pretrained", side_effect=AssertionError("local LLM loaded")):
                    agent = module.MedAgent(
                        name="test_agent", backbone=backbone_name, code_execution_config=False,
                        config_list=[{"model": "test/model"}],
                    )
                agent.register_dataset("eicu")
                with patch.object(agent, "retrieve_embedding_examples", return_value=("example", "knowledge example")), patch.object(
                    module, "call_llm", side_effect=["retrieved knowledge", "answer = 42"]
                ) as generate:
                    _, code, knowledge, _ = agent.initiate_chat(None, message="test question")
                self.assertEqual(code, "answer = 42")
                self.assertEqual(knowledge, "retrieved knowledge")
                self.assertTrue(all(call.kwargs["model"] == "test/model" for call in generate.call_args_list))
        finally:
            sys.path.remove(str(ROOT / "EhrAgent/ehragent"))

    def test_standalone_safety_api_parses_verdict_without_local_llm(self):
        module = load_module("safety_defenses", "certified-llm-safety/defenses.py")
        replies = iter([response("not harmful"), response("harmful")])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: next(replies))))
        tokenizer = lambda prompt: {"input_ids": [1, 2, 3, 4, 5, 6]}
        self.assertEqual(
            module.is_harmful(["benign request", "malicious request"], client, tokenizer, llm_name="OpenRouter"),
            [False, True],
        )

    def test_perplexity_requires_explicit_local_model(self):
        for directory in ("ReAct", "EhrAgent/ehragent"):
            with patch("transformers.AutoModelForCausalLM.from_pretrained") as loader:
                module = load_module("ppl_test", f"{directory}/perplexity.py")
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "full local"):
                        module.compute_perplexity_concurrent(["text"])
                loader.assert_not_called()

    def test_react_one_question_through_api_and_actual_wrappers(self):
        # Keep actual control flow and wrappers; isolate only retrieval weights/data.
        sys.path.insert(0, str(ROOT / "ReAct"))
        import local_wikienv
        import torch

        def init_database(env, embedder_name, trigger_sequence):
            env.database = {"test": {"content": "A benign memory."}}
            env.embedding_id = ["test"]
            env.db_embeddings = torch.tensor([[1.0, 0.0]])

        encoder = SimpleNamespace(to=lambda device: encoder, eval=lambda: None)
        runner = load_module("react_runner", "ReAct/run_strategyqa_gpt3.5.py")
        try:
            with tempfile.TemporaryDirectory() as output, patch.object(
                local_wikienv.AutoTokenizer, "from_pretrained"
            ), patch.object(
                local_wikienv.DPRContextEncoder, "from_pretrained", return_value=encoder
            ), patch.object(local_wikienv.WikiEnv, "load_db", init_database), patch(
                "transformers.AutoModelForCausalLM.from_pretrained", side_effect=AssertionError("local LLM loaded")
            ), patch.object(runner, "call_llm", side_effect=["I can answer this question.", "Finish[yes]"]) as generate, patch.object(
                sys, "argv", ["react", "--backbone", "openrouter", "--max-examples", "1", "--save_dir", output]
            ):
                runner.main()
                rows = [json.loads(line) for file in Path(output).glob("*.jsonl") for line in file.read_text().splitlines()]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["answer"], "yes")
                self.assertEqual(generate.call_count, 2)
        finally:
            sys.path.remove(str(ROOT / "ReAct"))


if __name__ == "__main__":
    unittest.main()
