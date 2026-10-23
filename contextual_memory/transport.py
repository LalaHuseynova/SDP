"""Bounded OpenRouter calls and exact-prompt memoization; no task labels as input."""

import hashlib
import json
from pathlib import Path
import time

from openai.types.chat import ChatCompletion


class TransportError(RuntimeError):
    pass


def prompt_key(messages, model):
    return hashlib.sha256(json.dumps(dict(model=model, messages=messages), sort_keys=True).encode()).hexdigest()


class RecordedCompletion:
    def __init__(self, run, model, *, max_calls=400, max_tokens=1024, max_input_bytes=24000, replay=None):
        self.run, self.model = Path(run), model
        self.max_calls, self.max_tokens, self.max_input_bytes = max_calls, max_tokens, max_input_bytes
        self.cache, self.events, self.hits = {}, [], []
        self.binding = {}
        self.failures = 0
        self.replay = replay
        self.replay_index = 0

    def __call__(self, messages):
        key = prompt_key(messages, self.model)
        if key in self.cache:
            self.hits.append(dict(**self.binding, key=key, original_call=self.cache[key][0]))
            return self.cache[key][1]
        if self.failures >= 2 or len(self.events) >= self.max_calls:
            raise TransportError("API budget exhausted or repeated transport failure.")
        bound = sum(len(m["content"].encode("utf-8")) + 32 for m in messages)
        if bound > self.max_input_bytes:
            raise TransportError("Input exceeds the experiment budget.")
        event = dict(**self.binding, call=len(self.events) + 1, key=key,
                     requested_model=self.model, messages=messages, max_tokens=self.max_tokens,
                     input_token_bound=bound, temperature=0)
        started = time.monotonic()
        try:
            if self.replay is not None:
                if self.replay_index >= len(self.replay):
                    raise AssertionError("Replay exhausted.")
                saved = self.replay[self.replay_index]
                self.replay_index += 1
                assert all(saved[k] == event[k] for k in ("case_id", "configuration", "key", "messages", "requested_model"))
                if saved["status"] != "ok":
                    raise TransportError("Saved API failure.")
                result = ChatCompletion.model_validate(saved["response"])
            else:
                from openrouter_client import get_client
                result = get_client().with_options(max_retries=0, timeout=60).chat.completions.create(
                    model=self.model, messages=messages, temperature=0, max_tokens=self.max_tokens)
            event.update(status="ok", response=result.model_dump(), returned_model=result.model,
                         usage=result.usage.model_dump() if result.usage else None)
            if not result.choices or result.choices[0].finish_reason != "stop" or not (result.choices[0].message.content or "").strip():
                raise TransportError("Incomplete model response.")
            self.cache[key] = event["call"], result
            self.failures = 0
            return result
        except Exception as exc:
            event.update(status="failure", error_type=type(exc).__name__)
            self.failures += 1
            raise TransportError("OpenRouter operation failed; no checker result accepted.") from None
        finally:
            event["wall_seconds"] = round(time.monotonic() - started, 3)
            self.events.append(event)
            if self.replay is None:
                with (self.run / "api.jsonl").open("a") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
