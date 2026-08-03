# KIT processed result 独立语义复核

> **SUPERSEDED / HISTORICAL — 不代表当前状态。** 本文保存的是 paired B/D_CORE readiness
> 之前的语义复核。当前权威状态是：development OOF 已完成 54/54 fits、2,919 个相同 eligible
> snapshots 和 23,352 条 OOF predictions；O2=0，且没有 selection、protected test、publication
> 或 deployment。结果不能证明统一赢家或 EIS 因果效应。KIT 是唯一活动项目；CALCE 已停止，
> Cambridge 仅为冻结历史材料。请以 `docs/live-early-warning-tasks.md` 为准。

日期：2026-08-01

结论：`GO-A（仅限 capacity-sequence F1 构造）`；`NO-GO（O2、D_CORE EIS 对齐、训练与发布）`

## 一句话结论

官方 DOI 10.35097/1969 的 processed EOC 数据足以把每个电芯的标准容量检查结果排成唯一
序列，并合法构造“第三个后续已完成容量检查”的 F1 目标；这个目标不需要 wall-clock
timestamp。此前“必须先证明 timestamp + duration 才能构造 H3”的阻断可以解除。

这不是训练放行：现有 readiness artifact 仍记录旧的“源文件缺失”状态，v2 engine/runtime
仍是 BLOCK，O2 的 5 pp 阈值仍没有技术重复噪声依据，EIS 与 capacity 的同次 check-up 对齐
仍需单独验证。

## 复核证据

只读检查了：

- 官方 result-data 包及 BagIt 清单；
- `Readme v2.pdf`；
- `Data Structure v05.3.xlsx` 的 `EOCV2`、`StateValues` 等 schema；
- 官方 `cell_eocv2.zip` 和 `cell_eisv2.zip`；
- repo 的 target-readiness 审计、protocol 和当前 parser 规则。

关键官方语义：

- `EOCV2` 是 modified/postprocessed end-of-charge 文件；EOC 记录在每次充/放电过程结束时
  收集并汇总该过程。
- `cyc_condition=2` 对应 `CHECKUP_RT`：在室温 check-up 中测量参考容量。
- `cyc_charged=0` 表示最后过程为放电。
- `cap_aged_est_Ah` 是把最后一次过程换算成 100%→0%、cut-off current 为 0 A 假设下的
  **估计容量**；它是 processed/derived 结果，不是未经处理的原始库仑计数。
- `timestamp_s` 是 Unix timestamp；`timestamp_origin=1` 是 `INFLUX_EXACT`。
- `cyc_duration_s` 是最后过程的持续时间，可能包含暂停/故障/重启影响，不能再加到
  EOC timestamp 上制造第二个“完成时间”。
- `num_cycles_checkup` 是已进行 check-up 的计数；官方 README 的 CU 编号与文件中的零起始
  counter 不应未经说明直接互换。

官方 capacity archive SHA-256：
`95de03af7d73efdeed97fcd13fbdca2eeabe85b2971b08394b665c8639b3e140`。

## 五个问题的明确判断

### 1. cell 内 check-up 顺序是否唯一？

**是，针对通过 endpoint rule 的 completed capacity results。**

独立扫描 228 个真实 cell 文件，以：

```text
cyc_condition == CHECKUP_RT (2)
and cyc_charged == false (0)
and finite cap_aged_est_Ah
and finite soh_cap
```

得到 3,999 个 endpoint：

- 每个 cell 的 `num_cycles_checkup` 无重复；
- 每个 cell 内 counter 严格递增；
- 对应 EOC timestamps 严格递增；
- 3,999 个 endpoint 的 `timestamp_origin` 全部为 1 (`INFLUX_EXACT`)；
- 228 个 cell 都有唯一 counter 0 endpoint。

一个非阻断例外：`P073-3` 的 completed endpoint counter 为
`0..11, 13`，缺少 12。因此 H3 必须定义为**序列中的第三个后续 completed endpoint**，
不能实现为 `checkup_counter + 3`。

### 2. capacity endpoint 是否可视为 completed check-up 结果？

**可以，但必须称为标准 check-up 放电后的 processed/estimated capacity result。**

支持理由是 EOC 的官方“过程结束时汇总”定义、`CHECKUP_RT` 状态、放电方向以及有限的
`cap_aged_est_Ah`。3,999 个入选 endpoint 的 protocol 字段一致：4.2→2.5 V、1 A 放电、
0.15 A cut-off 设置。官方 README 也说明容量结果来自室温 100%→0% 的 1/3 C 放电过程。

不能把它描述为原始容量或完全无假设的真实值。`cap_aged_est_Ah` 对起止 SoC/cut-off 做了
换算；异常长 `cyc_duration_s` 需要质量标记，但不能据此把 endpoint timestamp 后移。

### 3. H3 sequence target 能否不依赖 wall-clock timestamp 合法构造？

**可以。**

对每个 cell 按唯一 `num_cycles_checkup` 排列上述 completed endpoints。对序列位置 `i`，
future endpoint 是 `i+3`：

```text
SoH_i = 100 * capacity_i / Q0_cell
soh_loss_pp_h3 = SoH_i - SoH_(i+3)
```

这只依赖 cell identity、completed endpoint rule 和顺序，不依赖 elapsed seconds。共有 204
个 cell 至少有 4 个 endpoint；可形成 3,318 个 H3-eligible snapshots。其余 24 个 cell 没有
任何 H3-eligible snapshot，但仍保留在 inventory 并标记 `no H3 follow-up`。

wall-clock timestamp 仍可用于 lineage、EIS past-only alignment 和真实 lead-time 描述，但不是
F1 标签合法性的前提。

### 4. Q0 早期定义是否无泄漏？

**是，条件是把 Q0 冻结为每个 cell 唯一的 counter 0 completed capacity endpoint。**

228/228 cell 都有该 endpoint，容量范围约 2.926306–2.970551 Ah。它在老化序列开端已经
完成，对之后 snapshot 是 past-available；它不是 lifetime maximum，也不查看未来。

建议 endpoint rule 明写 `num_cycles_checkup == 0`，并保留 archive/member/line/counter/value
provenance。当前“按最早 timestamp 取第一条”的实现虽然在本包得到相同记录，但科学定义
应以唯一 CU0 result 为主，timestamp 只作一致性校验。

### 5. 哪些限制需要披露但不阻断？

- H3 是第三个后续 completed check-up，不是固定天数、EFC 或公里数；不同 ageing mode 和
  cell 的物理 horizon 不同。
- `P073-3` 有 counter gap；序列位置和计划 CU 编号必须分开。
- 24 个短历史 cell 没有 H3 target；不得将其变成零损失。
- `cap_aged_est_Ah` 是 processed estimate；source `soh_cap` 采用不同缩放定义，不能冒充
  `100 * capacity / Q0`。
- 官方 README 记录 reversible-capacity side experiment、CU24 附近异常和最后 manual CU；
  signed negative loss必须保留，不能裁剪。
- 真实天数/EFC/距离只作分层解释；不存在时写 unavailable，不能从 check-up index 推算。
- capacity endpoint 语义通过不等于 EIS retry/acquisition alignment 已通过。`D_CORE` 必须
  另做同次、past-only spectrum 对齐复核。
- 本结论仅支持 development target construction；不支持 O2 阈值、预测 interval、模型性能、
  action threshold、RUL、安全或部署结论。

## 最终 GO / NO-GO

### GO

- 解除“capacity F1 必须等待 wall-clock completion 语义”的阻断；
- 允许更新 Wave A readiness：用唯一 CU0 Q0 和第三个后续 completed capacity endpoint 构造
  signed F1；
- 允许 capacity-only snapshot/target audit 在 development 条件上继续。

### 仍然 NO-GO

- O2 `loss >= 5 pp`：没有技术重复测量噪声与决策价值依据；
- `D_CORE`：EIS 与 capacity snapshot 的 acquisition/retry 对齐尚未被本审查批准；
- v2 model run：必须先用恢复的官方包重新生成 readiness artifact，并通过 strict v2
  engine/runtime、独立 QA 和 human run gate；
- Accepted OOF UI、protected evaluation、公开结果和 action/replacement claim。

因此准确状态是：

> `GO-A for capacity-sequence F1 semantics; implementation/runtime/model execution remain blocked.`
