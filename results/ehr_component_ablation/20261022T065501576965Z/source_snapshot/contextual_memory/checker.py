"""Load the existing EHR checker without changing it or global module bindings."""

from functools import lru_cache
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def checker_class():
    directory = ROOT / "EhrAgent/ehragent"
    previous_path = list(sys.path)
    missing = object()
    previous_env = sys.modules.get("llm_env", missing)
    try:
        env_spec = importlib.util.spec_from_file_location("_contextual_llm_env", directory / "llm_env.py")
        env = importlib.util.module_from_spec(env_spec)
        env_spec.loader.exec_module(env)
        sys.modules["llm_env"] = env
        spec = importlib.util.spec_from_file_location("_contextual_amemguard_checker", directory / "consistency.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ConsistencyChecker
    finally:
        sys.path[:] = previous_path
        if previous_env is missing:
            sys.modules.pop("llm_env", None)
        else:
            sys.modules["llm_env"] = previous_env


class CompletionProvider:
    """The checker's normal provider interface, with bounded recorded transport injected."""
    def __init__(self, completion):
        self.completion = completion

    def generate_batch(self, prompts, token_stats=False):
        texts, stats = [], {"input_tokens": 0, "output_tokens": 0}
        for prompt in prompts:
            response = self.completion([
                dict(role="system", content="You are a helpful and precise assistant for logical analysis and text generation."),
                dict(role="user", content=prompt),
            ])
            texts.append(response.choices[0].message.content.strip())
            if token_stats and response.usage:
                stats["input_tokens"] += response.usage.prompt_tokens
                stats["output_tokens"] += response.usage.completion_tokens
        return texts, stats


def build_checker(completion):
    return checker_class()(CompletionProvider(completion))
