# 把 A 股看板的 Max Dama 研究门槛接入 Vibe-Trading

本适配器连接两个已经分工明确的系统：

```text
a-share-rank-tracker
  main_rise_watch.json
  main_rise_audit.json#max_dama_research_gate
  main_rise_outcomes.json
              ↓
a_share_rank_research_snapshot
  固化源文件、Git commit、研究门槛与仓位上限
              ↓
~/.vibe-trading/a-share-rank-research/<run_id>/
              ↓
max_dama_audit
```

## 单一计算来源

Alpha 样本、固定成本后的净超额收益、Alpha 衰减、执行证据、事件级风险和容量代理，统一由 `a-share-rank-tracker` 的盘后流水线生成：

```text
main_rise_audit.json → max_dama_research_gate
```

Vibe 适配器不再重复计算这些指标。它只做三件事：

1. 核验日期、Git commit、工作树和源文件哈希；
2. 把看板研究门槛固化成可复现的 Vibe run；
3. 将该 run 交给 `max_dama_audit` 做研究／晋级审计。

这样可以避免看板与 Agent 使用不同的样本、半衰期或容量口径。

## 安全边界

- 不改写 `main_rise_watch` 候选状态；
- 不调整策略阈值；
- 不读取模型提交的任意文件路径或命令；
- 不连接券商、钱包或下单接口；
- 尚未成熟的 horizon 保持 `pending`，不当作失败样本；
- 不可用和异常完整结果单独记录；
- 容量始终是成交额／参与率诊断，未经验证不称为可执行容量；
- 任何研究警告都会阻止 promotion；
- `live_execution_allowed` 固定为 `false`。

## 前置条件

A 股仓库必须已经运行：

```bash
python main_rise_audit_job.py finalize
python cost_aware_audit_job.py --round-trip-cost-bps 20
python max_dama_dashboard_job.py
```

完成后，`main_rise_audit.json` 中应存在：

```json
{
  "max_dama_research_gate": {
    "method": "max_dama_research_gate_v1",
    "policies": {
      "live_execution_allowed": false
    }
  }
}
```

若该门槛缺失、日期不一致或未明确关闭实盘，Vibe 快照会拒绝生成。

## 安装

在 Vibe-Trading 仓库根目录执行：

```bash
bash examples/a_share_rank_research_adapter/setup.sh \
  /你的绝对路径/a-share-rank-tracker
```

安装脚本会创建：

```text
~/.vibe-trading/a_share_rank_research.json
```

并把以下引擎目录加入已有的 `max_dama_research.json`：

```json
{
  "engine_roots": {
    "a_share_rank_tracker": "~/.vibe-trading/a-share-rank-research"
  }
}
```

配置示例：

```json
{
  "repo_root": "/ABSOLUTE/PATH/TO/a-share-rank-tracker",
  "output_root": "~/.vibe-trading/a-share-rank-research",
  "max_source_bytes": 25000000,
  "max_position_cap": 0.20,
  "single_name_cap": 0.05,
  "require_clean_worktree": true
}
```

路径和上限只能由操作员配置，模型调用时不能覆盖。

## 使用顺序

```text
a_share_rank_research_info
→ a_share_rank_research_snapshot(as_of="YYYYMMDD")
→ max_dama_audit(
     engine="a_share_rank_tracker",
     run_id="快照返回的 run_id",
     audit_level="research"
   )
```

需要检查晋级门槛时，可以把 `audit_level` 改为 `promotion`。只要样本、执行、组合风险、成本压力或容量仍有待补证据，结果就会是 `blocked`。

## 每次快照产物

```text
request.json
process.json
source_manifest.json
result.normalized.json
event_outcomes.normalized.json
cost_capacity_diagnostics.json
```

`run_id` 由以下稳定输入确定：

- A 股仓库 Git commit；
- 三份源文件 SHA-256；
- 看板 gate 的 `run_id`；
- 不含本地绝对路径的适配器参数。

因此，相同代码、数据与配置在不同本地目录中会得到相同 `run_id`。

## 合理的当前结果

样本仍在积累时，常见结果是：

```text
可复现输入          pass
时间一致性          pass
自动调参关闭        pass
实盘执行关闭        pass
主周期样本          warn
成本后净 Alpha      warn
Alpha 半衰期        warn
执行证据            warn
组合风险            warn
容量证据            warn
promotion           blocked
```

这表示研究链已经接通，但证据尚不足以晋级；它不是策略失败，也不是买卖建议。
