#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /absolute/path/to/a-share-rank-tracker" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
a_share_root="$(cd "$1" && pwd)"
config_dir="${HOME}/.vibe-trading"
adapter_config="${config_dir}/a_share_rank_research.json"
max_config="${config_dir}/max_dama_research.json"
output_root="${config_dir}/a-share-rank-research"

[[ -f "${a_share_root}/main_rise_watch.json" ]] || {
  echo "error: main_rise_watch.json not found under ${a_share_root}" >&2
  exit 1
}
[[ -f "${repo_root}/agent/src/tools/a_share_rank_research_tool.py" ]] || {
  echo "error: adapter source is missing from this Vibe-Trading checkout" >&2
  exit 1
}

mkdir -p "${config_dir}" "${output_root}"
python - "${a_share_root}" "${output_root}" \
  "${repo_root}/examples/a_share_rank_research_adapter/a_share_rank_research.example.json" \
  "${adapter_config}" <<'PY'
import json
import sys
from pathlib import Path

source_repo, output_root, example_path, destination = map(Path, sys.argv[1:])
payload = json.loads(example_path.read_text(encoding="utf-8"))
payload["repo_root"] = str(source_repo.resolve())
payload["output_root"] = str(output_root.resolve())
destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "${adapter_config}"

if [[ -f "${max_config}" ]]; then
  python - "${max_config}" "${output_root}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
output_root = str(Path(sys.argv[2]).resolve())
payload = json.loads(path.read_text(encoding="utf-8"))
roots = payload.setdefault("engine_roots", {})
roots["a_share_rank_tracker"] = output_root
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  chmod 600 "${max_config}"
  echo "updated ${max_config} with a_share_rank_tracker"
else
  echo "warning: ${max_config} does not exist; install max_dama_research first" >&2
fi

PYTHONPATH="${repo_root}/agent" python -m py_compile \
  "${repo_root}/agent/src/tools/a_share_rank_research_tool.py" \
  "${repo_root}/agent/src/tools/max_dama_research_tool.py"

echo "created ${adapter_config}"
echo "A-share research adapter installed (research-only; no live execution)."
