from __future__ import annotations

import json
from pathlib import Path

from src.tools.a_share_rank_research_tool import build_snapshot


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_build_snapshot_is_research_only(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    write(repo / "main_rise_watch.json", {
        "as_of": "20260717",
        "pools": {"breakout_watch": [{"security_id": "000001.SZ"}], "pre_breakout_watch": [], "technical_confirmed": [], "overheat_isolation": [], "unknown": [], "rejected": []},
    })
    write(repo / "main_rise_audit.json", {"as_of": "20260717", "validation": {"status": "collecting"}})
    write(repo / "main_rise_outcomes.json", {
        "as_of": "20260717",
        "events": {"x": {"state": "breakout_watch", "horizons": {"5": {"status": "complete", "entry_open": 10, "target_close": 11, "return_pct": 10, "excess_return_pct": 8}}}},
    })
    config = tmp_path / ".vibe-trading" / "a_share_rank_research.json"
    write(config, {
        "repo_root": str(repo), "output_root": str(tmp_path / "runs"), "primary_horizon": 5,
        "min_positive_events": 30, "min_decay_events_per_horizon": 30,
        "cost_scenarios": {"baseline": {"buy_cost_bps": 8, "sell_cost_bps": 13, "description": "test"}},
        "capacity_participation_rates": [0.01], "impact_coefficient_bps": 25,
        "max_position_cap": 0.2, "single_name_cap": 0.05, "require_clean_worktree": False,
    })
    monkeypatch.setattr("src.tools.a_share_rank_research_tool._config_path", lambda: config)
    monkeypatch.setattr("src.tools.a_share_rank_research_tool._git", lambda _repo: {"commit": "abc", "worktree_clean": True})
    result = build_snapshot("20260717")
    assert result["engine"] == "a_share_rank_tracker"
    assert result["metrics"]["primary_horizon_positive_events"] == 1
    assert result["risk"]["new_total_exposure_cap"] == 0.2
    assert result["policies"]["live_execution_allowed"] is False
    assert (Path(config.parent / "a-share-rank-research") / result["run_id"] / "result.normalized.json").is_file()
