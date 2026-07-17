# 把 a-share-rank-tracker 接入 Max Dama 研究审计

这个适配器连接你现有的两部分：

```text
a-share-rank-tracker
  main_rise_watch.json
  main_rise_audit.json
  main_rise_outcomes.json
  history/main_rise_watch/*.json
          ↓
a_share_rank_research_snapshot
  成本情景 + Alpha 衰减 + 流动性/容量代理 + 仓位上限
          ↓
~/.vibe-trading/a-share-rank-research/<run_id>/result.normalized.json
          ↓
max_dama_audit
```

它不会重算或改写当日候选，不会调阈值，也不会连接券商或生成订单。原项目仍然是盘后条件观察；新增层只把已有的前瞻结果变成统一、可审计的研究证据。

## 一、具体提取了什么

### 1. Alpha 契约

把当前互斥状态固定为：

- `breakout_watch`
- `pre_breakout_watch`
- `technical_confirmed`
- `overheat_isolation` 仅作为隔离对照，不进入正向 Alpha 池

预测口径沿用原框架：盘后形成信号，下一交易日开盘为参考入场，跟踪 1、3、5、10 个交易日。适配器按状态和周期汇总毛收益、超额收益、MFE、MAE，并在样本足够后尝试估计净超额收益的半衰期。

### 2. 成本后结果

原 `main_rise_outcomes.json` 是描述性毛收益。适配器在独立产物中应用操作员配置的成本情景：

```text
净收益 = 目标收盘价 × (1 - 卖出成本)
        / [参考开盘价 × (1 + 买入成本)] - 1
```

默认示例有 `baseline / stress / severe` 三档，但这些数字只是本地研究假设：它们不声称等于当前法定税费、你的券商佣金或真实成交滑点。你应按自己的账户和成交记录维护配置。

### 3. 容量诊断

从信号日归档快照读取 `technical.turnover_cny_20d`，计算 1%、5%、10% 市场成交参与率下的单标的名义资金代理，并生成平方根冲击代理：

```text
冲击 bps 代理 = impact_coefficient_bps × sqrt(参与率)
```

这不是策略容量结论。日均成交额不能替代盘口深度、开盘集合竞价、涨跌停、停牌、部分成交和并发持仓分析。因此输出固定标为 `diagnostic_only`，晋级审计会拒绝把它当成实盘级容量证据。

### 4. 风险与仓位

适配器保留当前前端仓位层级的含义：

- 无正向候选：`0%`
- 仅技术确认：上限 `5%`
- 突破前观察：上限 `10%`
- 收盘突破观察：上限 `20%`
- 覆盖不足、未知项或单一候选集中时自动降档
- 单一标的上限由配置控制，示例为 `5%`

这仍是**下一交易日新增总敞口的规则上限**，不是凯利仓位。因为当前没有重叠持仓路径、组合净值、实际成交和尾部故障回放，所以风险证据固定标为 `event_level_only`。

## 二、配置

先安装 Max Dama 共同审计层，再执行：

```bash
bash examples/a_share_rank_research_adapter/setup.sh \
  /你的绝对路径/a-share-rank-tracker
```

它会创建：

```text
~/.vibe-trading/a_share_rank_research.json
```

并在已有的 `~/.vibe-trading/max_dama_research.json` 中增加：

```json
{
  "engine_roots": {
    "a_share_rank_tracker": "~/.vibe-trading/a-share-rank-research"
  }
}
```

模型不能提交仓库路径、输出路径、成本值、容量系数或任意命令；这些均由操作员配置控制。工具只接受一个可选的 `as_of=YYYYMMDD` 日期护栏，而且必须与当前三份源文件的日期完全一致。

## 三、使用顺序

对话中输入：

```text
先调用 a_share_rank_research_info，核验固定仓库、Git commit、工作树状态、
成本情景和容量参数。然后调用 a_share_rank_research_snapshot。
把返回的 engine=a_share_rank_tracker 与 run_id 交给 max_dama_audit，
先使用 research 级别。成交、容量、组合回撤和凯利证据未独立验证前，
不得使用 promotion 结果，更不得授权实盘。
```

等价流程：

```text
a_share_rank_research_info
→ a_share_rank_research_snapshot(as_of="YYYYMMDD")
→ max_dama_audit(
     engine="a_share_rank_tracker",
     run_id="返回值",
     audit_level="research"
   )
```

## 四、每次快照写出的产物

```text
request.json
process.json
source_manifest.json
result.normalized.json
event_outcomes.normalized.json
cost_capacity_diagnostics.json
```

`run_id` 由源日期、源文件哈希和研究配置哈希确定。相同代码、数据和配置重复运行会落到同一个可复现目录。

`source_manifest.json` 记录：

- a-share-rank-tracker Git commit；
- 工作树是否干净；
- 三份当前文件与所有已使用历史快照的 SHA-256；
- 成本、容量和仓位配置的 SHA-256。

默认要求源仓库工作树干净，否则拒绝生成快照。

## 五、审计状态怎么解释

在现阶段，合理结果通常是：

```text
时间一致性             pass
前瞻/滚动样本外         pass
参数政策               pass（阈值冻结、自动调参关闭）
Alpha 半衰期            warn（样本不足时）
执行                   warn（成本诊断有，实际成交未验证）
风险                   warn（事件级 MAE 有，组合回撤/凯利没有）
容量                   warn（ADV 代理有，真实容量没有）
有效样本数             warn（低于门槛时）
净经济性               warn（主周期样本未成熟时不输出结论）
```

这正是预期行为：把“已有证据”和“仍缺证据”分开，而不是为了得到绿色结果而伪造精度。

## 六、从 collecting 到 promotion 还缺什么

只有补齐以下独立证据，才适合讨论晋级：

1. 主周期至少达到配置的独立正向事件门槛；
2. 参数邻域或冻结版本的滚动样本外比较；
3. 涨跌停、停牌、集合竞价、开盘不可成交与部分成交模拟；
4. 实际券商成交与模型成本的逐笔对账；
5. 并发持仓、行业集中、组合净值与最大回撤；
6. 压力情景下的尾部损失和分数凯利/风险预算；
7. 资金规模变化时，净 Alpha 随参与率和冲击的边际曲线。

适配器不会自动放宽门槛，也不会用当前少量样本回写 `main_rise_watch` 阈值。
