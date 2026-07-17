from __future__ import annotations

import json
from pathlib import Path

from src.tools.max_dama_research_tool import audit


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_audit_blocks_promotion_when_evidence_is_immature(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    run_id = "sample_run"
    write(root / run_id / "result.normalized.json", {
        "as_of": "20260717",
        "metrics": {"validation_status": "collecting", "primary_horizon_positive_events": 1, "missing_or_incomplete_rows": 2},
        "risk": {"validation_status": "event_level_only"},
        "capacity": {"validation_status": "diagnostic_only"},
        "policies": {"automatic_tuning": "disabled", "live_execution_allowed": False},
    })
    config = tmp_path / "max_dama_research.json"
    write(config, {"engine_roots": {"a_share_rank_tracker": str(root)}, "minimum_primary_events": 30})
    monkeypatch.setattr("src.tools.max_dama_research_tool._config_path", lambda: config)
    result = audit("a_share_rank_tracker", run_id, "promotion")
    assert result["overall"] == "blocked"
    assert result["promotion_ready"] is False
    assert result["live_execution_allowed"] is False
    assert result["counts"]["fail"] == 0
    assert result["counts"]["warn"] >= 1
