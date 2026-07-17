from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.max_dama_research_tool import audit


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def normalized_result(run_id: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "engine": "a_share_rank_tracker",
        "run_id": run_id,
        "as_of": "20260717",
        "metrics": {
            "validation_status": "collecting",
            "primary_horizon": 5,
            "primary_horizon_positive_events": 30,
            "minimum_primary_events": 30,
            "pending_rows": 4,
            "unavailable_rows": 0,
            "malformed_complete_rows": 0,
            "cost_adjusted": {
                "dashboard_fixed_cost": {
                    "5": {
                        "events": 30,
                        "mean_net_excess_return_pct": 0.7,
                    }
                }
            },
            "alpha_decay": {
                "dashboard_fixed_cost": {
                    "status": "estimated",
                    "half_life_sessions": 3.2,
                }
            },
        },
        "execution": {
            "validation_status": "research_assumptions_only",
            "broker_fills_verified": False,
            "cost_scenarios": {
                "dashboard_fixed_cost": {"round_trip_cost_bps": 20}
            },
        },
        "risk": {
            "validation_status": "event_level_only",
            "primary_horizon_median_mae_pct": -2.3,
        },
        "capacity": {
            "validation_status": "diagnostic_only",
            "turnover_coverage_pct": 75,
        },
        "policies": {
            "automatic_tuning": "disabled",
            "live_execution_allowed": False,
        },
    }


def test_audit_blocks_promotion_until_execution_risk_and_capacity_are_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "runs"
    run_id = "sample_run"
    run = root / run_id
    write(run / "result.normalized.json", normalized_result(run_id))
    write(run / "source_manifest.json", {"source": "test"})
    write(run / "process.json", {"status": "complete"})
    config = tmp_path / "max_dama_research.json"
    write(
        config,
        {
            "engine_roots": {"a_share_rank_tracker": str(root)},
            "minimum_primary_events": 30,
        },
    )
    monkeypatch.setattr(
        "src.tools.max_dama_research_tool._config_path", lambda: config
    )

    result = audit("a_share_rank_tracker", run_id, "promotion")
    assert result["overall"] == "blocked"
    assert result["promotion_ready"] is False
    assert result["live_execution_allowed"] is False
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["effective_sample"]["status"] == "pass"
    assert checks["net_economics"]["status"] == "pass"
    assert checks["alpha_decay"]["status"] == "pass"
    assert checks["execution_evidence"]["status"] == "warn"
    assert checks["portfolio_risk"]["status"] == "warn"
    assert checks["capacity_evidence"]["status"] == "warn"
    assert checks["complete_case_bias"]["status"] == "pass"


def test_pending_rows_do_not_count_as_complete_case_bias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "runs"
    run_id = "pending_ok"
    run = root / run_id
    value = normalized_result(run_id)
    value["metrics"]["pending_rows"] = 99
    write(run / "result.normalized.json", value)
    write(run / "source_manifest.json", {})
    write(run / "process.json", {})
    config = tmp_path / "max.json"
    write(config, {"engine_roots": {"a_share_rank_tracker": str(root)}})
    monkeypatch.setattr(
        "src.tools.max_dama_research_tool._config_path", lambda: config
    )
    result = audit("a_share_rank_tracker", run_id)
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["complete_case_bias"]["status"] == "pass"


def test_identity_mismatch_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "runs"
    run = root / "abc"
    write(
        run / "result.normalized.json",
        {"engine": "wrong", "run_id": "abc"},
    )
    write(run / "source_manifest.json", {})
    write(run / "process.json", {})
    config = tmp_path / "max.json"
    write(config, {"engine_roots": {"a_share_rank_tracker": str(root)}})
    monkeypatch.setattr(
        "src.tools.max_dama_research_tool._config_path", lambda: config
    )
    with pytest.raises(ValueError, match="identity"):
        audit("a_share_rank_tracker", "abc")
