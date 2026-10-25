"""Shared, lazy OpenRouter client. Importing this module never loads a model."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Resolve relative to the repository, even when a script is launched elsewhere.
# Existing environment variables take precedence over .env.
load_dotenv(Path(__file__).resolve().with_name(".env"))


def get_model(model=None):
    """An explicit model overrides OPENROUTER_MODEL and then the free router."""
    return model or os.getenv("OPENROUTER_MODEL") or "openrouter/free"


def get_max_tokens():
    """Budget includes hidden reasoning tokens on reasoning-capable providers."""
    try:
        value = int(os.getenv("OPENROUTER_MAX_TOKENS", "4096"))
    except ValueError:
        raise ValueError("OPENROUTER_MAX_TOKENS must be a positive integer.") from None
    if value <= 0:
        raise ValueError("OPENROUTER_MAX_TOKENS must be a positive integer.")
    return value


@lru_cache(maxsize=1)
def get_client():
    if not os.getenv("OPENROUTER_API_KEY"):
        raise ValueError("Set OPENROUTER_API_KEY in the repository's .env file.")
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=60.0,
        max_retries=2,
    )


def chat_completion(messages, model=None, max_tokens=None, temperature=0, **kwargs):
    """Return the API response, retaining real usage and optional logprobs.

    Errors propagate to the caller; an API failure is never generated text.
    This interface provides no local weights, full logits, or gradients.
    """
    response = get_client().chat.completions.create(
        model=get_model(model),
        messages=messages,
        max_tokens=get_max_tokens() if max_tokens is None else max_tokens,
        temperature=temperature,
        **kwargs,
    )
    if not response.choices or not (response.choices[0].message.content or "").strip():
        finish = response.choices[0].finish_reason if response.choices else "no choices"
        raise RuntimeError(
            f"OpenRouter returned no text (finish_reason={finish}). "
            "Increase OPENROUTER_MAX_TOKENS or select a non-reasoning OPENROUTER_MODEL."
        )
    return response


def call_llm(prompt, model=None, system=None, **kwargs):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat_completion(messages, model=model, **kwargs).choices[0].message.content
