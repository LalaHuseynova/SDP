#!/usr/bin/env bash
set -eu
# Run from the repository root; model/key settings are read from .env.
exec python ReAct/run_strategyqa_gpt3.5.py --backbone openrouter --model dpr --task_type adv --trigger "${TRIGGER:?Set TRIGGER to your optimized trigger text}" "$@"
