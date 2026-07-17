#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
config_dir="${HOME}/.vibe-trading"
config_path="${config_dir}/max_dama_research.json"
mkdir -p "${config_dir}" "${config_dir}/a-share-rank-research"
cp "${repo_root}/examples/max_dama_research_protocol/max_dama_research.example.json" "${config_path}"
chmod 600 "${config_path}"
PYTHONPATH="${repo_root}/agent" python -m py_compile \
  "${repo_root}/agent/src/tools/max_dama_research_tool.py" \
  "${repo_root}/agent/src/tools/a_share_rank_research_tool.py"
echo "created ${config_path}"
echo "Max Dama research gate installed (research-only; no live execution)."
