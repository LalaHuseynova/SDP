#!/usr/bin/env bash
set -eu
# Legacy script name; choose the remote model using OPENROUTER_MODEL in .env.
exec python ReAct/run_strategyqa_gpt3.5.py --backbone openrouter --model dpr --task_type adv --trigger "${TRIGGER:?Set TRIGGER to your optimized trigger text}" "$@"
