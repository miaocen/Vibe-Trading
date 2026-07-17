"""Max Dama evidence gate for reproducible research runs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from src.agent.tools import BaseTool

CONFIG_NAME = "max_dama_research.json"


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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config() -> dict[str, Any]:
    config = _load(_config_path())
    roots = config.get("engine_roots")
    if not isinstance(roots, dict) or not roots:
        raise ValueError("engine_roots must be a non-empty object")
    config["minimum_primary_events"] = int(
        config.get("minimum_primary_events", 30)
    )
    if config["minimum_primary_events"] < 1:
        raise ValueError("minimum_primary_events must be positive")
    return config


def _scenario_metric(
    metrics: Mapping[str, Any], scenario: str, horizon: int
) -> Mapping[str, Any]:
    cost = (
        metrics.get("cost_adjusted")
        if isinstance(metrics.get("cost_adjusted"), Mapping)
        else {}
    )
    scenario_value = cost.get(scenario) if isinstance(cost.get(scenario), Mapping) else {}
    value = scenario_value.get(str(horizon))
    return value if isinstance(value, Mapping) else {}


def audit(engine: str, run_id: str, audit_level: str = "research") -> dict[str, Any]:
    config = _config()
    if audit_level not in {"research", "promotion"}:
        raise ValueError("audit_level must be research or promotion")
    root_value = config["engine_roots"].get(engine)
    if not root_value:
        raise ValueError(f"engine is not configured: {engine}")
    if not run_id or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for character in run_id
    ):
        raise ValueError("invalid run_id")

    run_dir = Path(root_value).expanduser().resolve() / run_id
    result_path = run_dir / "result.normalized.json"
    manifest_path = run_dir / "source_manifest.json"
    process_path = run_dir / "process.json"
    if not result_path.is_file():
        raise ValueError(f"normalized result not found: {result_path}")
    result = _load(result_path)
    if result.get("engine") != engine or result.get("run_id") != run_id:
        raise ValueError(
            "normalized result identity does not match requested engine/run_id"
        )

    metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
    execution = (
        result.get("execution") if isinstance(result.get("execution"), Mapping) else {}
    )
    risk = result.get("risk") if isinstance(result.get("risk"), Mapping) else {}
    capacity = (
        result.get("capacity") if isinstance(result.get("capacity"), Mapping) else {}
    )
    policies = (
        result.get("policies") if isinstance(result.get("policies"), Mapping) else {}
    )
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    reproducible = manifest_path.is_file() and process_path.is_file()
    add(
        "reproducible_input",
        "pass" if reproducible else "fail",
        (
            f"result_sha256={_sha(result_path)}; "
            f"manifest={manifest_path.is_file()}; process={process_path.is_file()}"
        ),
    )
    add(
        "time_consistency",
        "pass" if result.get("as_of") else "fail",
        f"as_of={result.get('as_of')}",
    )
    validation = str(metrics.get("validation_status") or "collecting")
    add(
        "out_of_sample_maturity",
        "pass" if validation == "informative_sample" else "warn",
        f"validation_status={validation}",
    )

    primary = int(metrics.get("primary_horizon") or 0)
    primary_events = int(metrics.get("primary_horizon_positive_events") or 0)
    minimum = max(
        int(metrics.get("minimum_primary_events") or 0),
        int(config.get("minimum_primary_events", 30)),
    )
    sample_ready = primary_events >= minimum
    add(
        "effective_sample",
        "pass" if sample_ready else "warn",
        (
            f"primary_horizon={primary}; primary_events={primary_events}; "
            f"minimum={minimum}"
        ),
    )

    unavailable = int(metrics.get("unavailable_rows") or 0)
    malformed = int(metrics.get("malformed_complete_rows") or 0)
    pending = int(metrics.get("pending_rows") or 0)
    add(
        "complete_case_bias",
        "pass" if unavailable == 0 and malformed == 0 else "warn",
        (
            f"unavailable_rows={unavailable}; "
            f"malformed_complete_rows={malformed}; pending_rows={pending}"
        ),
    )

    scenario_names = list(execution.get("cost_scenarios") or {})
    baseline_name = (
        "baseline"
        if "baseline" in scenario_names
        else scenario_names[0]
        if scenario_names
        else ""
    )
    baseline = _scenario_metric(metrics, baseline_name, primary) if baseline_name else {}
    baseline_events = int(baseline.get("events") or 0)
    baseline_alpha = baseline.get("mean_net_excess_return_pct")
    if baseline_events < minimum:
        economic_status = "warn"
    elif isinstance(baseline_alpha, (int, float)) and baseline_alpha > 0:
        economic_status = "pass"
    else:
        economic_status = "fail"
    add(
        "net_economics",
        economic_status,
        (
            f"scenario={baseline_name or 'none'}; events={baseline_events}; "
            f"mean_net_excess_return_pct={baseline_alpha}"
        ),
    )

    decay_root = (
        metrics.get("alpha_decay")
        if isinstance(metrics.get("alpha_decay"), Mapping)
        else {}
    )
    decay = (
        decay_root.get(baseline_name)
        if isinstance(decay_root.get(baseline_name), Mapping)
        else {}
    )
    add(
        "alpha_decay",
        "pass" if decay.get("status") == "estimated" else "warn",
        (
            f"status={decay.get('status', 'unavailable')}; "
            f"half_life_sessions={decay.get('half_life_sessions')}"
        ),
    )

    stress_names = [name for name in scenario_names if name != baseline_name]
    stress_ready = bool(stress_names)
    stress_pass = stress_ready
    stress_details: list[str] = []
    for name in stress_names:
        value = _scenario_metric(metrics, name, primary)
        events = int(value.get("events") or 0)
        alpha = value.get("mean_net_excess_return_pct")
        stress_details.append(f"{name}:events={events},alpha={alpha}")
        if (
            events < minimum
            or not isinstance(alpha, (int, float))
            or alpha <= 0
        ):
            stress_pass = False
    add(
        "cost_stress",
        "pass" if stress_ready and stress_pass else "warn",
        "; ".join(stress_details) if stress_details else "no non-baseline cost scenario",
    )

    add(
        "execution_evidence",
        "pass" if execution.get("validation_status") == "validated" else "warn",
        (
            f"execution_status={execution.get('validation_status')}; "
            f"broker_fills_verified={execution.get('broker_fills_verified')}"
        ),
    )
    add(
        "portfolio_risk",
        "pass" if risk.get("validation_status") == "portfolio_level" else "warn",
        (
            f"risk_status={risk.get('validation_status')}; "
            f"median_mae={risk.get('primary_horizon_median_mae_pct')}"
        ),
    )
    add(
        "capacity_evidence",
        "pass" if capacity.get("validation_status") == "validated" else "warn",
        (
            f"capacity_status={capacity.get('validation_status')}; "
            f"turnover_coverage_pct={capacity.get('turnover_coverage_pct')}"
        ),
    )
    add(
        "automatic_tuning",
        "pass" if policies.get("automatic_tuning") == "disabled" else "fail",
        f"automatic_tuning={policies.get('automatic_tuning')}",
    )
    add(
        "live_execution",
        "pass" if policies.get("live_execution_allowed") is False else "fail",
        f"live_execution_allowed={policies.get('live_execution_allowed')}",
    )

    fail_count = sum(item["status"] == "fail" for item in checks)
    warn_count = sum(item["status"] == "warn" for item in checks)
    promotion_ready = fail_count == 0 and warn_count == 0
    if audit_level == "promotion" and not promotion_ready:
        overall = "blocked"
    elif fail_count:
        overall = "fail"
    elif warn_count:
        overall = "research_only"
    else:
        overall = "pass"
    return {
        "schema_version": 2,
        "engine": engine,
        "run_id": run_id,
        "as_of": result.get("as_of"),
        "audit_level": audit_level,
        "overall": overall,
        "counts": {
            "pass": sum(item["status"] == "pass" for item in checks),
            "warn": warn_count,
            "fail": fail_count,
        },
        "checks": checks,
        "promotion_ready": promotion_ready,
        "live_execution_allowed": False,
        "boundary": "research evidence audit; not a trading instruction",
    }


class MaxDamaResearchInfoTool(BaseTool):
    name = "max_dama_research_info"
    description = "Inspect configured research engines and Max Dama evidence thresholds."
    parameters = {"type": "object", "properties": {}, "required": []}
    repeatable = True
    is_readonly = True

    def execute(self, **_: Any) -> str:
        try:
            config = _config()
            return json.dumps(
                {
                    "status": "ok",
                    "config_path": str(_config_path()),
                    "engines": sorted(config["engine_roots"]),
                    "minimum_primary_events": config["minimum_primary_events"],
                    "live_execution_allowed": False,
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


class MaxDamaAuditTool(BaseTool):
    name = "max_dama_audit"
    description = "Audit a fixed normalized research run for sample, cost, execution, risk, capacity and live-safety evidence."
    parameters = {
        "type": "object",
        "properties": {
            "engine": {"type": "string"},
            "run_id": {"type": "string"},
            "audit_level": {"type": "string", "enum": ["research", "promotion"]},
        },
        "required": ["engine", "run_id"],
        "additionalProperties": False,
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        try:
            return json.dumps(
                {
                    "status": "ok",
                    **audit(
                        str(kwargs["engine"]),
                        str(kwargs["run_id"]),
                        str(kwargs.get("audit_level", "research")),
                    ),
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


__all__ = ["MaxDamaResearchInfoTool", "MaxDamaAuditTool", "audit"]
