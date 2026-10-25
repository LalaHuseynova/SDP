"""Bounded, recorded OpenRouter calls for this EHR experiment only."""
import json
import os
import threading
import time
from pathlib import Path
from openrouter_client import get_client, get_model, OPENROUTER_BASE_URL

_LOCK=threading.Lock()
_STATE={'attempts':0,'tokens':0,'reserved_tokens':0,'consecutive_failures':0}

def chat_completion(messages, model=None, max_tokens=None, temperature=0, **kwargs):
    max_tokens=int(max_tokens or os.environ.get('EHR_MAX_OUTPUT_TOKENS','1536'))
    # UTF-8 bytes upper-bound the number of tokenizer tokens, without downloading another model.
    input_bound=sum(len(m['content'].encode('utf-8'))+32 for m in messages)
    if input_bound>int(os.environ.get('EHR_MAX_INPUT_TOKEN_BOUND','32000')):
        raise RuntimeError('Input token upper bound exceeded')
    reservation=input_bound+max_tokens
    with _LOCK:
        if _STATE['consecutive_failures']>=2:
            raise RuntimeError('Stopped after two consecutive failed API operations')
        if _STATE['attempts']>=int(os.environ.get('EHR_MAX_CALLS','400')):
            raise RuntimeError('API call budget exhausted')
        if _STATE['reserved_tokens']+reservation>int(os.environ.get('EHR_MAX_TOKEN_BOUND','8000000')):
            raise RuntimeError('Token upper-bound budget exhausted')
        _STATE['attempts']+=1; _STATE['reserved_tokens']+=reservation
        number=_STATE['attempts']
    kwargs = dict(seed=0, top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0) | kwargs
    start=time.monotonic()
    event=dict(call=number,endpoint=OPENROUTER_BASE_URL,requested_model=get_model(model),temperature=temperature,
               max_tokens=max_tokens,input_token_upper_bound=input_bound,generation_kwargs=kwargs,messages=messages)
    try:
        response=get_client().with_options(max_retries=0,timeout=60).chat.completions.create(
            model=get_model(model),messages=messages,max_tokens=max_tokens,temperature=temperature,**kwargs)
        event.update(actual_model=response.model,usage=response.usage.model_dump() if response.usage else None,
                     response=response.model_dump(),status='ok')
        if not response.choices or not (response.choices[0].message.content or '').strip():
            raise ValueError('Empty API output')
        if response.choices[0].finish_reason!='stop':
            raise ValueError('Incomplete API output: '+str(response.choices[0].finish_reason))
        with _LOCK:
            _STATE['tokens']+=response.usage.total_tokens if response.usage else 0
            _STATE['consecutive_failures']=0
        return response
    except Exception as exc:
        # Never store exception strings that might contain request credentials.
        event.update(status='failure',error_type=type(exc).__name__)
        with _LOCK:_STATE['consecutive_failures']+=1
        raise
    finally:
        event['wall_seconds']=time.monotonic()-start
        path=os.getenv('EHR_API_LOG')
        if path:
            with _LOCK,Path(path).open('a') as f:f.write(json.dumps(event)+'\n')

def call_llm(prompt,model=None,system=None,**kwargs):
    messages=([dict(role='system',content=system)] if system else [])+[dict(role='user',content=prompt)]
    return chat_completion(messages,model=model,**kwargs).choices[0].message.content.strip()
