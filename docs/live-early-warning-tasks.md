# KIT 主项目：当前状态与下一步

更新：2026-08-02
状态：`paired B/D_CORE condition-held-out development OOF completed`
范围：**KIT only；CALCE 备用项目已停止**

## 大白话状态

我们现在回答的是：

> 在同一个电芯、同一个检查点、同一个留出 fold、同一个模型家族下，加入当前检查点的 EIS，
> 对“未来第三次检查会损失多少 SoH 百分点”的预测有没有帮助？

正式开发回放已经完成，但答案不是一个简单的“有用 / 没用”：

- cyclic + HGB：出现改善信号；
- cyclic + ElasticNet：出现变差信号；
- calendar：没有明确变化；
- profile：ElasticNet 点估计较好但区间跨 0；HGB 的 MAE 变差而 RMSE 不明确。

因此不能说 EIS 普遍提高预测，也不能宣布 HGB 或 ElasticNet 是最终模型。

## 已完成的权威 development run

权威 run：

`artifacts/local/paired-runs/kit-f1-paired-b-dcore-dev-v2-20260802T201053-cdt`

| 项目 | 结果 |
|---|---:|
| 真实 KIT 电芯 | 228 |
| Operating conditions | 76 |
| 容量 endpoints | 3,999 |
| Paired eligible cells | 180 |
| Paired eligible snapshots | 2,919 |
| 完成 fits | 54 / 54 |
| OOF prediction rows | 23,352 |
| Aggregate metric rows | 24 |
| B/D_CORE paired deltas | 12 |
| Retry-sensitivity rows | 6 |
| O2 fits / predictions | 0 / 0 |

所有 12 个差值都使用 2,000 次 whole-condition paired bootstrap；它们是开发诊断抽样，不是
2,000 个新电芯，也不是因果显著性检验。Primary 与 keep-last sensitivity 的结果相同，但
timestamp/retry 语义仍标为 provisional。

## 永久保留的边界

```text
scientific_result=false
formal_model_result=false
protected_test_result=false
publication_ready=false
selection_performed=false
resume_approved=false
deployment_ready=false
```

允许展示：经独立重算一致的 development OOF 指标、配对差值、区间和历史回放。
禁止宣称：protected/external/final test、生产报警、故障或热失控预测、RUL、换电时间、成本收益、
通用赢家、EIS 因果效应或论文结论。

## 当前工作队列

| 优先级 | 工作 | Gate / 完成证据 | 状态 |
|---:|---|---|---|
| P0 | 将 paired metrics/deltas 接入 228-cell UI | 24/12/6 exact rows；无 composite-key 覆盖；UI contract tests | completed |
| P0 | 同步 README、研究报告、文章与过程文档 | 不保留过时 B-only 当前状态；数字与 derived JSON 一致 | completed |
| P0 | 浏览器 desktop/mobile 视觉 QA | 截图、无 overflow、交互可用、限制条常驻 | completed |
| P1 | 独立科学/claim review | 禁止表述扫描；所有 flags 继续 false | completed |
| P1 | 决定下一轮科学问题 | 先解释 mode × family interaction，不开放 protected test | pending |
| P2 | Protected/external evaluation | 需要新的冻结协议、独立批准和一次性 label gate | not authorized |
| P2 | Publication / deployment | 独立 publication review 与部署验证 | not authorized |

## 失败 run 记录

第一次 paired 尝试在完成 42/54 fits 后失败，失败目录被永久保留，没有续跑或补写。证据支持
ElasticNet 在病态 D_CORE 特征矩阵上耗尽原迭代预算；我们用 synthetic stress reproducer 验证了
一个只改变 `max_iter` 的最小修复，再创建了全新的 run lineage。该机制有强证据支持，但不应被
夸大为对所有机器、所有数据都成立的普遍根因。

## 项目选择

- 主项目：KIT battery ageing early-warning benchmark。
- CALCE：已停止，不下载、不训练、不作为 runtime/config 依赖；只可在历史决策记录中说明曾被
  考虑过。
- Cambridge：保留为独立历史 benchmark，不得与 KIT 指标混合。
