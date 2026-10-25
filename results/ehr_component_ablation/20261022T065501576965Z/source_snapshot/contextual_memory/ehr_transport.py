"""Recorded OpenRouter transport for compiler, native EHR checker and generation."""

import hashlib
import json
from pathlib import Path
import time

from openai.types.chat import ChatCompletion


class EHRTransportError(RuntimeError):
    pass


class EHRCompletion:
    def __init__(self, run, model, *, replay=None, max_calls=400, max_tokens=1536, resume=False):
        self.run, self.model, self.replay = Path(run), model, replay
        self.events, self.hits, self.cache = [], [], {}
        self.binding, self.failures, self.position = {}, 0, 0
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.resume = resume

    def __call__(self, messages, model=None, **kwargs):
        if model is not None and model != self.model:
            raise EHRTransportError("Model differs from the frozen experiment.")
        parameters = dict(temperature=0, max_tokens=self.max_tokens) | kwargs
        key = hashlib.sha256(json.dumps(dict(messages=messages, model=self.model, parameters=parameters),sort_keys=True).encode()).hexdigest()
        if key in self.cache:
            call, response = self.cache[key]
            self.hits.append(dict(**self.binding, key=key, original_call=call))
            return response
        bound = sum(len(m["content"].encode())+32 for m in messages)
        if len(self.events) >= self.max_calls or self.failures >= 2 or bound > 32000:
            raise EHRTransportError("Experiment budget or failure circuit reached.")
        event = dict(**self.binding, call=len(self.events)+1, key=key, model=self.model,
                     messages=messages, parameters=parameters, input_token_bound=bound)
        started = time.monotonic()
        try:
            if self.replay is not None and (self.position < len(self.replay) or not self.resume):
                if self.position >= len(self.replay):
                    raise ValueError("Replay exhausted.")
                saved = self.replay[self.position]
                self.position += 1
                if any(event[k] != saved[k] for k in ("case_id","configuration","condition","phase","key","messages","parameters")):
                    raise ValueError("Replay inputs differ.")
                if saved["status"] != "ok":
                    raise EHRTransportError("Saved API failure.")
                response = ChatCompletion.model_validate(saved["response"])
                event["origin"] = "reused_prefix" if self.resume else saved.get("origin", "live")
            else:
                from openrouter_client import get_client
                response = get_client().with_options(max_retries=0,timeout=60).chat.completions.create(
                    model=self.model,messages=messages,**parameters)
                event["origin"] = "live"
            event.update(status="ok",response=response.model_dump(),
                         usage=response.usage.model_dump() if response.usage else None)
            if not response.choices or response.choices[0].finish_reason != "stop" or not (response.choices[0].message.content or "").strip():
                raise EHRTransportError("Empty or incomplete response.")
            self.cache[key] = event["call"], response
            self.failures = 0
            return response
        except Exception as exc:
            self.failures += 1
            event.update(status="failure",error_type=type(exc).__name__)
            raise EHRTransportError("Recorded model operation failed.") from None
        finally:
            event["wall_seconds"] = round(time.monotonic()-started,3)
            self.events.append(event)
            if self.replay is None or self.resume:
                with (self.run / "api.jsonl").open("a") as f:
                    f.write(json.dumps(event,ensure_ascii=False)+"\n")
