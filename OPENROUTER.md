# Running A-MemGuard with OpenRouter

The ReAct and EHRAgent backbones, memory auditor, and A-MemGuard consistency
checker now send generation requests to OpenRouter. They do not load local Llama
weights. `gpt` and `llama3` backbone arguments remain as compatibility aliases for
OpenRouter. The consistency modules retain explicitly selected `hf`, `openai`,
and `vllm` providers for existing library users.

The shared client follows the [OpenRouter OpenAI SDK integration](https://openrouter.ai/docs/quickstart#using-the-openai-sdk).
It reads the repository's `.env` even if the working directory is different.
Existing environment variables override `.env`. The key is never stored in code.

## Setup on an 8 GB Mac

From the repository root, use Python 3.10 or newer:

```bash
source .venv/bin/activate
python -m pip install -r requirements-openrouter.txt
```

This dependency file avoids the NVIDIA/CUDA and bitsandbytes dependencies in the
original research environment. Add the following to your existing `.env` without
replacing your working key:

```dotenv
OPENROUTER_API_KEY=your-existing-key
OPENROUTER_MODEL=openrouter/free
OPENROUTER_MAX_TOKENS=4096
```

`OPENROUTER_MAX_TOKENS` defaults to 4096 and includes hidden reasoning as well
as visible output. Increase it for reasoning-heavy models if the API reports
`finish_reason=length` without text; no reasoning content is substituted for an
answer. See [OpenRouter reasoning token budgets](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

`OPENROUTER_MODEL` accepts an OpenRouter model slug. If omitted, the client uses
`openrouter/free`, matching the original working connectivity test. This router
may select different underlying models, and free providers can impose rate
limits. For reproducible experiments, configure a particular model. API and empty
response errors propagate; the application does not silently load a local model
or substitute a generated answer.

## Commands

Check connectivity with one short request:

```bash
python test_openrouter.py
```

Run the A-MemGuard consistency demo, with no PyTorch, Transformers, retriever,
or local model needed (uses the LLM method):

```bash
python ReAct/consistency.py --backend openrouter
```

Run one benign StrategyQA question with the A-MemGuard defense:

```bash
python ReAct/run_strategyqa_gpt3.5.py --backbone openrouter --model dpr --task_type benign --audit_method A-MemGuard --max-examples 1
```

Remove `--max-examples 1` for the full evaluation. Override the generation model
using `--llm-model <provider/model>`; `--model dpr` selects the **retriever**, not
the LLM. CLI model overrides also apply to the defense and auditor.

StrategyQA data is already present in this checkout. The first retrieval run
downloads the smaller `facebook/dpr-ctx_encoder-single-nq-base` encoder and
tokenizer, then builds `ReAct/database/embeddings/` if necessary. This can take
time on CPU. It does not download a causal LLM. Later runs reuse the embeddings.

For poisoning experiments, supply your own optimized trigger explicitly:

```bash
python ReAct/run_strategyqa_gpt3.5.py --backbone openrouter --model dpr --task_type adv --trigger 'your optimized trigger text' --audit_method A-MemGuard --max-examples 1
```

The legacy ReAct shell scripts also use OpenRouter. Supply the trigger through
`TRIGGER='your optimized trigger text' bash scripts/react_strategyqa/run_inference_gpt.sh`.

EHRAgent additionally needs its external memory logs under
`EhrAgent/database/ehr_logs/logs_final/`; these are **absent from this checkout**.
Existing embedding cache files do not supply the missing memory text. After
obtaining the dataset described in the main README:

```bash
python -m pip install autogen-agentchat==0.2.40 termcolor python-Levenshtein jsonlines
python EhrAgent/ehragent/main.py --backbone openrouter --model dpr --algo ap --audit_method A-MemGuard --num_questions 1
```

Use `--llm <provider/model>` to override EHRAgent's generation model. Its AutoGen
configuration, code generation, knowledge retrieval, and debugger all use
OpenRouter. The `ada` retriever remains a separate OpenAI embedding integration
requiring its own configuration/key; use `dpr` with only an OpenRouter key.

The standalone safety evaluator also supports `--llm_name OpenRouter` (its new
default). It uses only a GPT-2 **tokenizer** locally to erase tokens. Those token
boundaries differ from the remote model, so token-based experimental certificates
are not equivalent to the original Llama experiments. Explicit Llama selections
still load local weights. Foundational graph extraction uses the same OpenRouter
settings through its existing LangChain integration.

## Operations that remain local

OpenRouter chat generation cannot supply arbitrary input-token logits, model
weights, hidden states, or gradients. These operations were **not** approximated:

| File / function | Local access required |
| --- | --- |
| `ReAct/perplexity.py::compute_perplexity_concurrent` and `EhrAgent/ehragent/perplexity.py::compute_perplexity_concurrent` | Full next-token logits for input text and cross-entropy/perplexity. `--audit_method ppl` fails clearly unless you explicitly set `AMEMGUARD_LOCAL_PPL_MODEL` to a model ID/path; only then is that model loaded, on CPU. |
| `algo/utils.py::target_word_prob` and `EhrAgent/ehragent/utils.py::target_word_prob` | Full vocabulary logits for target-word probabilities. |
| `algo/trigger_optimization.py::compute_perplexity`, `hotflip_attack`, `GradientStorage`, and its optimization loop | Local causal model loss for coherence filtering (`--ppl_filter`), and local embedder weights/gradients for token optimization. `--target_gradient_guidance` without `--use_gpt` also loads a local target LLM. |
| `algo/utils.py::get_embeddings`, `load_models`, `bert_get_adv_emb`, `bert_get_cpa_emb` (and corresponding EHRAgent utilities) | Local embedder representations/weights. Explicit Llama/GPT-2 selections in `load_models` still load causal models for these research operations. |
| `algo/linear_embedder_optimization.py` training loop and `embedder/train_*_retriever.py` | Embedder training gradients; these research scripts retain their original device requirements. |
| `certified-llm-safety/grad_ec.py::grad_ec`, `greedy_grad_ec.py::greedy_grad_ec`, `gcg.py::gcg_suffix`, `greedy_ec.py::greedy_ec` | Local classifier embeddings, logits, and/or gradients. Training scripts (`adv_train.py`, `greedy_train.py`, `safety_classifier.py`) also require local models. |
| `ReAct/classifier.py::RandECDetector` and `EhrAgent/ehragent/classifier.py::RandECDetector` | The `distil` baseline needs a local DistilBERT classifier and trained checkpoint. It is loaded only when that baseline is chosen. |
| Both consistency modules: `HuggingFaceModel` / `ConsistencyChecker._get_st_model` | Explicit `--backend hf` loads a local LLM; `--method clustering` lazily loads a sentence encoder. The default OpenRouter + LLM method does neither. |

The normal `dpr` retriever in `ReAct/local_wikienv.py::WikiEnv` and
`EhrAgent/ehragent/medagent.py::MedAgent` remains local. It is a smaller encoder,
not a generative LLM, and now runs on CPU when CUDA is unavailable. Legacy
ReAct transition-score collection was removed from normal inference: those
scores were unused, and no substitute probabilities are fabricated. If you need
the original local `compute_transition_scores` analysis, it requires local model
access; this inference path no longer computes it.

## Verification

```bash
python -m unittest discover -s tests -v
python test_openrouter.py
```

The offline tests exercise actual SDK request serialization, error propagation,
model configuration, both auditors/consistency checkers, the explicit local-logit
limitation, the standalone safety filter, and a one-question ReAct run with real
wrappers and mocked retrieval weights. With the optional AutoGen dependencies,
they also test EHRAgent's OpenRouter and legacy Llama backbone paths. The live
test sends one short API request; it does not download a model.

## Changed files

- Shared client, configuration, and setup: `openrouter_client.py`, `.env.example`,
  `requirements-openrouter.txt`, `README.md`, `OPENROUTER.md`.
- ReAct: `ReAct/run_strategyqa_gpt3.5.py`, `ReAct/local_wikienv.py`,
  `ReAct/auditor.py`, `ReAct/consistency.py`, `ReAct/perplexity.py`,
  `scripts/react_strategyqa/run_inference_gpt.sh`,
  `scripts/react_strategyqa/run_inference_llama3.sh`.
- EHRAgent: `EhrAgent/ehragent/main.py`, `EhrAgent/ehragent/medagent.py`,
  `EhrAgent/ehragent/config.py`, `EhrAgent/ehragent/auditor.py`,
  `EhrAgent/ehragent/consistency.py`, `EhrAgent/ehragent/perplexity.py`,
  `EhrAgent/ehragent/utils.py`.
- Other inference: `algo/utils.py`, `foundational_exp/get_graph.py`,
  `certified-llm-safety/main.py`, `certified-llm-safety/defenses.py`.
- Verification: `test_openrouter.py`, `tests/test_openrouter_backend.py`.

Your existing `.env` was not changed.
