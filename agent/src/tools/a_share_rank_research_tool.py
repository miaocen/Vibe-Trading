"""Read-only bridge from the dashboard's Max Dama gate into Vibe research runs."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.agent.tools import BaseTool

CONFIG_NAME = "a_share_rank_research.json"
TOOL_VERSION = 2


def _config_path() -> Path:
    return Path.home() / ".vibe-trading" / CONFIG_NAME


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _git(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", "-C", str(repo), *args],
                text=True,
                stderr=subprocess.STDOUT,
                timeout=10,
            ).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"could not inspect source repository: {repo}") from exc

    return {
        "commit": run("rev-parse", "HEAD"),
        "worktree_clean": not bool(run("status", "--porcelain")),
    }


def _config() -> dict[str, Any]:
    config = _load(_config_path())
    allowed = {
        "repo_root",
        "output_root",
        "max_source_bytes",
        "max_position_cap",
        "single_name_cap",
        "require_clean_worktree",
        # Accepted for backward compatibility with the first draft. The dashboard
        # gate, not this adapter, is now the calculation source of truth.
        "primary_horizon",
        "min_positive_events",
        "min_decay_events_per_horizon",
        "cost_scenarios",
        "capacity_participation_rates",
        "impact_coefficient_bps",
    }
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise ValueError(f"unknown configuration fields: {', '.join(unknown)}")
    if not config.get("repo_root"):
        raise ValueError("repo_root is required")
    config["repo_root"] = str(Path(config["repo_root"]).expanduser().resolve())
    config["output_root"] = str(
        Path(config.get("output_root", "~/.vibe-trading/a-share-rank-research"))
        .expanduser()
        .resolve()
    )
    maximum = int(_number(config.get("max_source_bytes")) or 25_000_000)
    if maximum < 1:
        raise ValueError("max_source_bytes must be positive")
    config["max_source_bytes"] = maximum
    max_cap = _number(config.get("max_position_cap"))
    single_cap = _number(config.get("single_name_cap"))
    config["max_position_cap"] = 0.20 if max_cap is None else max_cap
    config["single_name_cap"] = 0.05 if single_cap is None else single_cap
    if not 0 <= config["max_position_cap"] <= 1:
        raise ValueError("max_position_cap must be between 0 and 1")
    if not 0 <= config["single_name_cap"] <= config["max_position_cap"]:
        raise ValueError("single_name_cap must be between 0 and max_position_cap")
    config["require_clean_worktree"] = bool(config.get("require_clean_worktree", True))
    return config


def _position_caps(watch: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[float, float]:
    decision = (
        watch.get("decision_summary")
        if isinstance(watch.get("decision_summary"), Mapping)
        else {}
    )
    guidance = (
        decision.get("position_guidance")
        if isinstance(decision.get("position_guidance"), Mapping)
        else {}
    )
    upper_pct = _number(guidance.get("upper_pct"))
    single_pct = _number(guidance.get("single_name_cap_pct"))
    if upper_pct is None:
        return 0.0, 0.0
    total = min(float(config["max_position_cap"]), max(0.0, upper_pct / 100))
    if single_pct is None:
        single = min(float(config["single_name_cap"]), total)
    else:
        single = min(
            float(config["single_name_cap"]),
            total,
            max(0.0, single_pct / 100),
        )
    return total, single


def _validate_gate(gate: Mapping[str, Any], source_date: str) -> None:
    if gate.get("method") != "max_dama_research_gate_v1":
        raise ValueError(
            "main_rise_audit.json lacks max_dama_research_gate_v1; "
            "merge/run the dashboard gate before creating a Vibe snapshot"
        )
    if str(gate.get("as_of") or "") != source_date:
        raise ValueError("Max Dama gate date does not match the watch date")
    if not isinstance(gate.get("checks"), list):
        raise ValueError("Max Dama gate lacks checks")
    policies = gate.get("policies") if isinstance(gate.get("policies"), Mapping) else {}
    if policies.get("live_execution_allowed") is not False:
        raise ValueError("Max Dama gate must explicitly disable live execution")


def build_snapshot(as_of: str | None = None) -> dict[str, Any]:
    config = _config()
    repo = Path(config["repo_root"])
    files = {
        "watch": repo / "main_rise_watch.json",
        "audit": repo / "main_rise_audit.json",
        "outcomes": repo / "main_rise_outcomes.json",
    }
    for path in files.values():
        if not path.is_file():
            raise ValueError(f"required source file missing: {path}")
        if path.stat().st_size > config["max_source_bytes"]:
            raise ValueError(f"source file exceeds configured size limit: {path}")

    watch = _load(files["watch"])
    audit = _load(files["audit"])
    outcomes = _load(files["outcomes"])
    source_date = str(watch.get("as_of") or watch.get("data_trade_date") or "")
    if not (len(source_date) == 8 and source_date.isdigit()):
        raise ValueError("watch artifact lacks YYYYMMDD as_of")
    if as_of and as_of != source_date:
        raise ValueError(f"as_of mismatch: requested {as_of}, source {source_date}")
    for payload, label in ((audit, "audit"), (outcomes, "outcomes")):
        dated = str(payload.get("as_of") or "")
        if dated and dated != source_date:
            raise ValueError(f"{label} date mismatch: {dated} != {source_date}")

    repository = _git(repo)
    if config["require_clean_worktree"] and not repository["worktree_clean"]:
        raise ValueError("source repository worktree is not clean")
    gate = (
        audit.get("max_dama_research_gate")
        if isinstance(audit.get("max_dama_research_gate"), Mapping)
        else {}
    )
    _validate_gate(gate, source_date)

    sample = gate.get("sample") if isinstance(gate.get("sample"), Mapping) else {}
    execution = gate.get("execution") if isinstance(gate.get("execution"), Mapping) else {}
    risk = gate.get("risk") if isinstance(gate.get("risk"), Mapping) else {}
    capacity = gate.get("capacity") if isinstance(gate.get("capacity"), Mapping) else {}
    gate_policies = gate.get("policies") if isinstance(gate.get("policies"), Mapping) else {}
    total_cap, single_cap = _position_caps(watch, config)
    fixed_cost_name = "dashboard_fixed_cost"
    fixed_cost = execution.get("round_trip_cost_bps")

    manifest = {
        name: {"path": str(path), "sha256": _sha(path)} for name, path in files.items()
    }
    stable_config = {
        "max_source_bytes": config["max_source_bytes"],
        "max_position_cap": config["max_position_cap"],
        "single_name_cap": config["single_name_cap"],
        "require_clean_worktree": config["require_clean_worktree"],
    }
    identity_input = {
        "tool_version": TOOL_VERSION,
        "date": source_date,
        "git_commit": repository["commit"],
        "source_hashes": {name: value["sha256"] for name, value in manifest.items()},
        "dashboard_gate_run_id": gate.get("run_id"),
        "adapter_parameters": stable_config,
    }
    run_id = _json_sha(identity_input)[:20]
    run_dir = Path(config["output_root"]) / run_id

    cost_positive = (
        gate.get("cost_adjusted_positive")
        if isinstance(gate.get("cost_adjusted_positive"), Mapping)
        else {}
    )
    alpha_decay = gate.get("alpha_decay") if isinstance(gate.get("alpha_decay"), Mapping) else {}
    result = {
        "schema_version": 2,
        "engine": "a_share_rank_tracker",
        "run_id": run_id,
        "as_of": source_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "repository": repository,
            "manifest": manifest,
            "dashboard_gate_run_id": gate.get("run_id"),
            "identity_sha256": _json_sha(identity_input),
        },
        "metrics": {
            "validation_status": sample.get("validation_status", "collecting"),
            "signal_days_with_completed_outcomes": int(
                sample.get("signal_days_with_completed_outcomes") or 0
            ),
            "positive_completed_event_horizons": int(
                sample.get("positive_completed_event_horizons") or 0
            ),
            "pending_rows": int(sample.get("pending_rows") or 0),
            "unavailable_rows": int(sample.get("unavailable_rows") or 0),
            "malformed_complete_rows": int(
                sample.get("malformed_complete_rows") or 0
            ),
            "primary_horizon": int(gate.get("primary_horizon") or 0),
            "primary_horizon_positive_events": int(
                sample.get("primary_horizon_positive_events") or 0
            ),
            "minimum_primary_events": int(gate.get("minimum_primary_events") or 0),
            "cost_adjusted": {fixed_cost_name: cost_positive},
            "alpha_decay": {fixed_cost_name: alpha_decay},
        },
        "execution": {
            **dict(execution),
            "cost_scenarios": {
                fixed_cost_name: {
                    "round_trip_cost_bps": fixed_cost,
                    "description": "fixed dashboard research assumption",
                }
            },
        },
        "risk": {
            **dict(risk),
            "new_total_exposure_cap": total_cap,
            "single_name_cap": single_cap,
        },
        "capacity": dict(capacity),
        "policies": {
            "automatic_tuning": gate_policies.get("automatic_tuning", "disabled"),
            "live_execution_allowed": False,
            "interpretation": "research_only_not_a_buy_signal",
        },
        "dashboard_gate": dict(gate),
    }

    _write(run_dir / "request.json", {"as_of": as_of, "resolved_as_of": source_date})
    _write(run_dir / "source_manifest.json", result["source"])
    _write(
        run_dir / "event_outcomes.normalized.json",
        {
            "source": "main_rise_audit.json#max_dama_research_gate",
            "cost_adjusted_positive": cost_positive,
        },
    )
    _write(
        run_dir / "cost_capacity_diagnostics.json",
        {
            "alpha_decay": alpha_decay,
            "execution": result["execution"],
            "capacity": result["capacity"],
        },
    )
    _write(run_dir / "result.normalized.json", result)
    _write(
        run_dir / "process.json",
        {
            "status": "complete",
            "tool_version": TOOL_VERSION,
            "run_id": run_id,
            "dashboard_gate_run_id": gate.get("run_id"),
            "result_sha256": _sha(run_dir / "result.normalized.json"),
            "live_execution_allowed": False,
        },
    )
    return result


class AShareRankResearchInfoTool(BaseTool):
    name = "a_share_rank_research_info"
    description = "Inspect the fixed dashboard-gate research adapter configuration."
    parameters = {"type": "object", "properties": {}, "required": []}
    repeatable = True
    is_readonly = True

    def execute(self, **_: Any) -> str:
        try:
            config = _config()
            repo = Path(config["repo_root"])
            return json.dumps(
                {
                    "status": "ok",
                    "configured": True,
                    "config_path": str(_config_path()),
                    "repo_root": str(repo),
                    "repository": _git(repo),
                    "output_root": config["output_root"],
                    "calculation_source": "main_rise_audit.json#max_dama_research_gate",
                    "research_only": True,
                    "live_execution_allowed": False,
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(
                {"status": "error", "configured": False, "error": str(exc)},
                ensure_ascii=False,
            )


class AShareRankResearchSnapshotTool(BaseTool):
    name = "a_share_rank_research_snapshot"
    description = "Freeze the dashboard Max Dama gate as a reproducible Vibe research run; no paths or orders accepted."
    parameters = {
        "type": "object",
        "properties": {"as_of": {"type": "string", "pattern": "^[0-9]{8}$"}},
        "required": [],
        "additionalProperties": False,
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        try:
            return json.dumps(
                {"status": "ok", **build_snapshot(kwargs.get("as_of"))},
                ensure_ascii=False,
                allow_nan=False,
            )
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


__all__ = [
    "AShareRankResearchInfoTool",
    "AShareRankResearchSnapshotTool",
    "build_snapshot",
]
