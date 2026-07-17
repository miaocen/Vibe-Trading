from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.a_share_rank_research_tool import build_snapshot


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def source_files(repo: Path, *, include_gate: bool = True) -> None:
    write(
        repo / "main_rise_watch.json",
        {
            "as_of": "20260717",
            "decision_summary": {
                "position_guidance": {
                    "upper_pct": 10,
                    "single_name_cap_pct": 5,
                }
            },
            "pools": {
                "breakout_watch": [],
                "pre_breakout_watch": [],
                "technical_confirmed": [],
                "overheat_isolation": [],
            },
        },
    )
    gate = {
        "schema_version": 1,
        "as_of": "20260717",
        "method": "max_dama_research_gate_v1",
        "run_id": "dashboard_gate_run",
        "overall": "research_only",
        "primary_horizon": 5,
        "minimum_primary_events": 30,
        "sample": {
            "validation_status": "collecting",
            "signal_days_with_completed_outcomes": 2,
            "positive_completed_event_horizons": 8,
            "primary_horizon_positive_events": 2,
            "pending_rows": 6,
            "unavailable_rows": 1,
            "malformed_complete_rows": 0,
        },
        "cost_adjusted_positive": {
            "1": {"events": 2, "mean_net_excess_return_pct": 1.2},
            "3": {"events": 2, "mean_net_excess_return_pct": 0.9},
            "5": {"events": 2, "mean_net_excess_return_pct": 0.5},
            "10": {"events": 0, "mean_net_excess_return_pct": None},
        },
        "alpha_decay": {
            "status": "collecting",
            "half_life_sessions": None,
        },
        "execution": {
            "validation_status": "research_assumptions_only",
            "round_trip_cost_bps": 20,
            "broker_fills_verified": False,
        },
        "risk": {
            "validation_status": "event_level_only",
            "primary_horizon_median_mae_pct": -2.5,
        },
        "capacity": {
            "validation_status": "diagnostic_only",
            "turnover_coverage_pct": 50,
            "estimated_capacity": None,
        },
        "checks": [{"name": "effective_sample", "status": "warn"}],
        "promotion": {"ready": False, "blockers": ["sample"]},
        "policies": {
            "automatic_tuning": "disabled",
            "live_execution_allowed": False,
        },
    }
    audit = {"as_of": "20260717"}
    if include_gate:
        audit["max_dama_research_gate"] = gate
    write(repo / "main_rise_audit.json", audit)
    write(repo / "main_rise_outcomes.json", {"as_of": "20260717", "events": {}})


def config(repo: Path, output: Path) -> dict[str, object]:
    return {
        "repo_root": str(repo),
        "output_root": str(output),
        "max_source_bytes": 1_000_000,
        "max_position_cap": 0.2,
        "single_name_cap": 0.05,
        "require_clean_worktree": False,
    }


def test_snapshot_freezes_dashboard_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "source"
    repo.mkdir()
    source_files(repo)
    output = tmp_path / "runs"
    adapter_config = tmp_path / "a_share_rank_research.json"
    write(adapter_config, config(repo, output))
    monkeypatch.setattr(
        "src.tools.a_share_rank_research_tool._config_path",
        lambda: adapter_config,
    )
    monkeypatch.setattr(
        "src.tools.a_share_rank_research_tool._git",
        lambda _repo: {"commit": "abc", "worktree_clean": True},
    )

    result = build_snapshot("20260717")
    assert result["schema_version"] == 2
    assert result["metrics"]["primary_horizon_positive_events"] == 2
    assert result["metrics"]["unavailable_rows"] == 1
    assert result["metrics"]["pending_rows"] == 6
    assert result["metrics"]["alpha_decay"]["dashboard_fixed_cost"]["status"] == "collecting"
    assert result["capacity"]["validation_status"] == "diagnostic_only"
    assert result["risk"]["new_total_exposure_cap"] == 0.10
    assert result["risk"]["single_name_cap"] == 0.05
    assert result["policies"]["live_execution_allowed"] is False

    run_dir = output / result["run_id"]
    for name in (
        "request.json",
        "process.json",
        "source_manifest.json",
        "result.normalized.json",
        "event_outcomes.normalized.json",
        "cost_capacity_diagnostics.json",
    ):
        assert (run_dir / name).is_file()


def test_run_id_ignores_local_output_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "source"
    repo.mkdir()
    source_files(repo)
    first_config = tmp_path / "one.json"
    second_config = tmp_path / "two.json"
    write(first_config, config(repo, tmp_path / "one"))
    write(second_config, config(repo, tmp_path / "two"))
    monkeypatch.setattr(
        "src.tools.a_share_rank_research_tool._git",
        lambda _repo: {"commit": "abc", "worktree_clean": True},
    )
    monkeypatch.setattr(
        "src.tools.a_share_rank_research_tool._config_path", lambda: first_config
    )
    first = build_snapshot()["run_id"]
    monkeypatch.setattr(
        "src.tools.a_share_rank_research_tool._config_path", lambda: second_config
    )
    second = build_snapshot()["run_id"]
    assert first == second


def test_snapshot_requires_dashboard_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "source"
    repo.mkdir()
    source_files(repo, include_gate=False)
    adapter_config = tmp_path / "config.json"
    write(adapter_config, config(repo, tmp_path / "runs"))
    monkeypatch.setattr(
        "src.tools.a_share_rank_research_tool._config_path", lambda: adapter_config
    )
    monkeypatch.setattr(
        "src.tools.a_share_rank_research_tool._git",
        lambda _repo: {"commit": "abc", "worktree_clean": True},
    )
    with pytest.raises(ValueError, match="dashboard gate"):
        build_snapshot()
