"""Read-only bridge from a-share-rank-tracker artifacts to Max Dama research evidence."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping

from src.agent.tools import BaseTool

CONFIG_NAME = "a_share_rank_research.json"
POSITIVE_STATES = ("breakout_watch", "pre_breakout_watch", "technical_confirmed")
HORIZONS = (1, 3, 5, 10)


def _config_path() -> Path:
    return Path.home() / ".vibe-trading" / CONFIG_NAME


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _git(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    return {"commit": run("rev-parse", "HEAD"), "worktree_clean": not bool(run("status", "--porcelain"))}


def _config() -> dict[str, Any]:
    cfg = _load(_config_path())
    allowed = {
        "repo_root", "output_root", "max_source_bytes", "primary_horizon",
        "min_positive_events", "min_decay_events_per_horizon", "cost_scenarios",
        "capacity_participation_rates", "impact_coefficient_bps", "max_position_cap",
        "single_name_cap", "require_clean_worktree",
    }
    unknown = sorted(set(cfg) - allowed)
    if unknown:
        raise ValueError(f"unknown configuration fields: {', '.join(unknown)}")
    cfg["repo_root"] = str(Path(cfg["repo_root"]).expanduser().resolve())
    cfg["output_root"] = str(Path(cfg.get("output_root", "~/.vibe-trading/a-share-rank-research")).expanduser().resolve())
    return cfg


def _net_return(entry: float, close: float, buy_bps: float, sell_bps: float) -> float:
    return 100 * ((close * (1 - sell_bps / 10000)) / (entry * (1 + buy_bps / 10000)) - 1)


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "events": len(values),
        "mean_pct": mean(values) if values else None,
        "median_pct": median(values) if values else None,
        "win_rate_pct": 100 * sum(v > 0 for v in values) / len(values) if values else None,
    }


def build_snapshot(as_of: str | None = None) -> dict[str, Any]:
    cfg = _config()
    repo = Path(cfg["repo_root"])
    files = {
        "watch": repo / "main_rise_watch.json",
        "audit": repo / "main_rise_audit.json",
        "outcomes": repo / "main_rise_outcomes.json",
    }
    for path in files.values():
        if not path.is_file():
            raise ValueError(f"required source file missing: {path}")
        if path.stat().st_size > int(cfg.get("max_source_bytes", 25_000_000)):
            raise ValueError(f"source file exceeds configured size limit: {path}")

    watch, audit, outcomes = (_load(files[key]) for key in ("watch", "audit", "outcomes"))
    source_date = str(watch.get("as_of") or watch.get("data_trade_date") or "")
    if not (len(source_date) == 8 and source_date.isdigit()):
        raise ValueError("watch artifact lacks YYYYMMDD as_of")
    if as_of and as_of != source_date:
        raise ValueError(f"as_of mismatch: requested {as_of}, source {source_date}")
    for payload, label in ((audit, "audit"), (outcomes, "outcomes")):
        dated = str(payload.get("as_of") or "")
        if dated and dated != source_date:
            raise ValueError(f"{label} date mismatch: {dated} != {source_date}")

    git = _git(repo)
    if cfg.get("require_clean_worktree", True) and not git["worktree_clean"]:
        raise ValueError("source repository worktree is not clean")

    events = outcomes.get("events") if isinstance(outcomes.get("events"), dict) else {}
    rows: list[dict[str, Any]] = []
    grouped: dict[str, dict[int, list[float]]] = {}
    missing = 0
    for event in events.values():
        if not isinstance(event, dict) or event.get("state") not in (*POSITIVE_STATES, "overheat_isolation"):
            continue
        state = str(event["state"])
        horizons = event.get("horizons") if isinstance(event.get("horizons"), dict) else {}
        for horizon in HORIZONS:
            result = horizons.get(str(horizon))
            if not isinstance(result, dict) or result.get("status") != "complete":
                missing += 1
                continue
            entry = _number(result.get("entry_open"))
            close = _number(result.get("target_close"))
            if not entry or not close:
                missing += 1
                continue
            item = {"state": state, "horizon": horizon, "return_pct": _number(result.get("return_pct")), "excess_return_pct": _number(result.get("excess_return_pct")), "cost_scenarios": {}}
            for name, scenario in cfg.get("cost_scenarios", {}).items():
                net = _net_return(entry, close, float(scenario["buy_cost_bps"]), float(scenario["sell_cost_bps"]))
                item["cost_scenarios"][name] = {"net_return_pct": net}
                if state in POSITIVE_STATES:
                    grouped.setdefault(name, {}).setdefault(horizon, []).append(net)
            rows.append(item)

    summaries = {name: {str(h): _summary(values.get(h, [])) for h in HORIZONS} for name, values in grouped.items()}
    validation = audit.get("validation") if isinstance(audit.get("validation"), dict) else {}
    primary = int(cfg.get("primary_horizon", 5))
    primary_events = sum(1 for row in rows if row["state"] in POSITIVE_STATES and row["horizon"] == primary)

    pool = watch.get("pools") if isinstance(watch.get("pools"), dict) else {}
    positive_count = sum(len(pool.get(state) or []) for state in POSITIVE_STATES)
    if len(pool.get("breakout_watch") or []) > 0:
        cap = min(float(cfg.get("max_position_cap", .20)), .20)
    elif len(pool.get("pre_breakout_watch") or []) > 0:
        cap = min(float(cfg.get("max_position_cap", .20)), .10)
    elif positive_count:
        cap = min(float(cfg.get("max_position_cap", .20)), .05)
    else:
        cap = 0.0

    manifest = {name: {"path": str(path), "sha256": _sha(path)} for name, path in files.items()}
    identity = hashlib.sha256(json.dumps({"date": source_date, "git": git, "manifest": manifest, "config": cfg}, sort_keys=True).encode()).hexdigest()[:20]
    run_dir = Path(cfg["output_root"]) / identity
    result = {
        "schema_version": 1,
        "engine": "a_share_rank_tracker",
        "run_id": identity,
        "as_of": source_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"repository": git, "manifest": manifest},
        "metrics": {
            "validation_status": validation.get("status", "collecting"),
            "positive_observations": positive_count,
            "completed_rows": len(rows),
            "missing_or_incomplete_rows": missing,
            "primary_horizon": primary,
            "primary_horizon_positive_events": primary_events,
            "cost_adjusted": summaries,
        },
        "risk": {"validation_status": "event_level_only", "new_total_exposure_cap": cap, "single_name_cap": float(cfg.get("single_name_cap", .05))},
        "capacity": {"validation_status": "diagnostic_only", "estimated_capacity": None, "participation_rates": cfg.get("capacity_participation_rates", [])},
        "policies": {"automatic_tuning": "disabled", "live_execution_allowed": False, "interpretation": "research_only_not_a_buy_signal"},
    }
    _write(run_dir / "request.json", {"as_of": as_of})
    _write(run_dir / "source_manifest.json", manifest)
    _write(run_dir / "event_outcomes.normalized.json", {"rows": rows})
    _write(run_dir / "result.normalized.json", result)
    return result


class AShareRankResearchInfoTool(BaseTool):
    name = "a_share_rank_research_info"
    description = "Inspect the fixed read-only a-share-rank-tracker research adapter configuration."
    parameters = {"type": "object", "properties": {}, "required": []}
    repeatable = True
    is_readonly = True

    def execute(self, **_: Any) -> str:
        try:
            cfg = _config()
            repo = Path(cfg["repo_root"])
            return json.dumps({"status": "ok", "configured": True, "config_path": str(_config_path()), "repo_root": str(repo), "repository": _git(repo), "output_root": cfg["output_root"], "research_only": True, "live_execution_allowed": False}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "configured": False, "error": str(exc)}, ensure_ascii=False)


class AShareRankResearchSnapshotTool(BaseTool):
    name = "a_share_rank_research_snapshot"
    description = "Create a reproducible cost-adjusted research snapshot from fixed A-share artifacts; no paths or orders accepted."
    parameters = {"type": "object", "properties": {"as_of": {"type": "string", "pattern": "^[0-9]{8}$"}}, "required": [], "additionalProperties": False}
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        try:
            return json.dumps({"status": "ok", **build_snapshot(kwargs.get("as_of"))}, ensure_ascii=False, allow_nan=False)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


__all__ = ["AShareRankResearchInfoTool", "AShareRankResearchSnapshotTool", "build_snapshot"]
