"""Max Dama evidence gate for reproducible research runs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.agent.tools import BaseTool

CONFIG_NAME = "max_dama_research.json"


def _config_path() -> Path:
    return Path.home() / ".vibe-trading" / CONFIG_NAME


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config() -> dict[str, Any]:
    cfg = _load(_config_path())
    roots = cfg.get("engine_roots")
    if not isinstance(roots, dict) or not roots:
        raise ValueError("engine_roots must be a non-empty object")
    return cfg


def audit(engine: str, run_id: str, audit_level: str = "research") -> dict[str, Any]:
    cfg = _config()
    if audit_level not in {"research", "promotion"}:
        raise ValueError("audit_level must be research or promotion")
    root_value = cfg["engine_roots"].get(engine)
    if not root_value:
        raise ValueError(f"engine is not configured: {engine}")
    if not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in run_id):
        raise ValueError("invalid run_id")
    run_dir = Path(root_value).expanduser().resolve() / run_id
    result_path = run_dir / "result.normalized.json"
    if not result_path.is_file():
        raise ValueError(f"normalized result not found: {result_path}")
    result = _load(result_path)
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    risk = result.get("risk") if isinstance(result.get("risk"), dict) else {}
    capacity = result.get("capacity") if isinstance(result.get("capacity"), dict) else {}
    policies = result.get("policies") if isinstance(result.get("policies"), dict) else {}

    checks: list[dict[str, Any]] = []
    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    add("reproducible_input", "pass", f"result_sha256={_sha(result_path)}")
    add("time_consistency", "pass" if result.get("as_of") else "fail", f"as_of={result.get('as_of')}")
    validation = str(metrics.get("validation_status") or "collecting")
    add("out_of_sample_maturity", "pass" if validation == "informative_sample" else "warn", f"validation_status={validation}")
    primary_events = int(metrics.get("primary_horizon_positive_events") or 0)
    minimum = int(cfg.get("minimum_primary_events", 30))
    add("effective_sample", "pass" if primary_events >= minimum else "warn", f"primary_events={primary_events}; minimum={minimum}")
    missing = int(metrics.get("missing_or_incomplete_rows") or 0)
    add("complete_case_bias", "pass" if missing == 0 else "warn", f"missing_or_incomplete_rows={missing}")
    add("execution_evidence", "warn", "cost scenarios are modeled; broker fills, auction execution, limits and partial fills are not independently verified")
    add("portfolio_risk", "pass" if risk.get("validation_status") == "portfolio_level" else "warn", f"risk_status={risk.get('validation_status')}")
    add("capacity_evidence", "pass" if capacity.get("validation_status") == "validated" else "warn", f"capacity_status={capacity.get('validation_status')}")
    add("automatic_tuning", "pass" if policies.get("automatic_tuning") == "disabled" else "fail", f"automatic_tuning={policies.get('automatic_tuning')}")
    add("live_execution", "pass" if policies.get("live_execution_allowed") is False else "fail", f"live_execution_allowed={policies.get('live_execution_allowed')}")

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
        "schema_version": 1,
        "engine": engine,
        "run_id": run_id,
        "audit_level": audit_level,
        "overall": overall,
        "counts": {"pass": sum(i["status"] == "pass" for i in checks), "warn": warn_count, "fail": fail_count},
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
            cfg = _config()
            return json.dumps({"status": "ok", "config_path": str(_config_path()), "engines": sorted(cfg["engine_roots"]), "minimum_primary_events": cfg.get("minimum_primary_events", 30), "live_execution_allowed": False}, ensure_ascii=False)
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
            return json.dumps({"status": "ok", **audit(str(kwargs["engine"]), str(kwargs["run_id"]), str(kwargs.get("audit_level", "research")))}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


__all__ = ["MaxDamaResearchInfoTool", "MaxDamaAuditTool", "audit"]
