"""Prefix reuse and offline replay through a real SDK HTTP mock, no model imports."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI
from contextual_memory.ehr_transport import EHRCompletion
import openrouter_client as backend


class ResumeTests(unittest.TestCase):
    def test_resume_verifies_prefix_then_calls_only_for_new_requests(self):
        calls=[]
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200,json=dict(id='resume',object='chat.completion',created=0,model='test/clinical',
                choices=[dict(index=0,finish_reason='stop',message=dict(role='assistant',content='{}'))],
                usage=dict(prompt_tokens=2,completion_tokens=1,total_tokens=3)))
        client=OpenAI(base_url=backend.OPENROUTER_BASE_URL,api_key='test-key',http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as temp,client,patch.object(backend,'get_client',return_value=client):
            a=Path(temp)/'a';b=Path(temp)/'b';a.mkdir();b.mkdir()
            binding=dict(case_id='one',configuration='test',condition='setup',phase='compile')
            first=EHRCompletion(a,'test/clinical');first.binding=binding
            one=[dict(role='user',content='First JSON request')]
            two=[dict(role='user',content='Second JSON request')]
            first(one)
            resumed=EHRCompletion(b,'test/clinical',replay=deepcopy(first.events),resume=True);resumed.binding=binding
            resumed(one);resumed(two)
            self.assertEqual(len(calls),2)
            self.assertEqual([e['origin'] for e in resumed.events],['reused_prefix','live'])
            replay=EHRCompletion(b,'test/clinical',replay=deepcopy(resumed.events));replay.binding=binding
            with patch.object(backend,'get_client',side_effect=AssertionError('no network')):
                replay(one);replay(two)
            self.assertEqual([e['origin'] for e in replay.events],['reused_prefix','live'])


if __name__=='__main__':unittest.main()
