# 加上 EIS，电池未来衰减预测一定会更好吗？

状态：**内部 development 文章草稿；不可发布**
证据边界：condition-held-out development OOF；不是 protected/external test
`publication_ready=false` · `selection_performed=false`

## 我想回答的问题

容量检查能告诉我们电池现在还有多少容量，但维护人员真正关心的是未来：

> 根据到当前检查点为止的信息，未来第三次检查时，SoH 会再下降多少个百分点？

EIS 看起来包含更多内部状态信息。问题是：把 EIS 加入已有的容量历史和工况后，预测是否真的更好？

## 数据是什么？

项目使用公开的 KIT battery ageing result data v2：228 个 commercial NMC/C+SiO 电芯，76 个
operating conditions，每个 condition 有三个 replicate cells。数据覆盖 calendar、controlled
cyclic 和 WLTP-derived driving-profile ageing，并在多次 check-up 中记录容量、EIS 和 pulse。

目标不是“坏 / 不坏”，而是连续数值：

```text
SoH_t = 100 × Capacity_t / Q0
future loss = SoH_t − SoH_t+3
```

`t+3` 指未来第三次 completed check-up，不是固定三天、三周或三个 cycle。

## 怎样做到公平比较？

我们冻结两个输入视图：

- `B`：当前和历史 SoH、近期下降速度、elapsed exposure 和已知 operating context；
- `D_CORE`：B 加上当前检查点匹配的 EIS 特征。

比较时固定相同的 snapshot、condition-held-out fold、sample weight 和模型家族。完整 condition
一起留出，避免同一工况的 replicate cells 同时出现在训练和验证中。

正式 development run 覆盖 228 个电芯和 3,999 个容量 endpoints，其中 180 个电芯、2,919 个
snapshots 同时满足 B、D_CORE、历史和 H3 truth 要求。运行完成 54/54 fits，得到 23,352 个 OOF
prediction rows。O2 快速衰减分类没有训练，protected test 没有打开。

## 模型为什么选 ElasticNet 和 HGB？

- ElasticNet：受正则化约束的线性模型，用来测试一组透明的线性组合是否足够；
- HGB：受约束的非线性树模型，用来表达有限的非线性和交互；
- persistence 和 recent slope：保留为 B-view 的简单基线，防止复杂模型只是在增加复杂度。

这些都是事先冻结的 development candidates，没有调参竞赛、ensemble 或 champion selection。

## 结果：没有统一答案

误差差值定义为：

```text
Δ = metric(D_CORE) − metric(B)
```

对于 MAE / RMSE，负数表示加入 EIS 后误差下降，正数表示误差上升。

| Mode | Model | B MAE | D_CORE MAE | ΔMAE | 95% whole-condition CI | 大白话 |
|---|---|---:|---:|---:|---:|---|
| Calendar | ElasticNet | 0.209 | 0.260 | +0.051 | [-0.020, +0.144] | 点估计变差，但不确定 |
| Calendar | HGB | 0.139 | 0.138 | -0.001 | [-0.013, +0.009] | 基本没有明确变化 |
| Cyclic | ElasticNet | 5.113 | 5.918 | +0.804 | [+0.285, +1.361] | 变差信号 |
| Cyclic | HGB | 5.809 | 5.184 | -0.625 | [-1.056, -0.241] | 改善信号 |
| Driving profile | ElasticNet | 4.791 | 4.461 | -0.330 | [-1.208, +0.433] | 点估计改善，但不确定 |
| Driving profile | HGB | 4.239 | 4.453 | +0.215 | [+0.036, +0.384] | MAE 变差；RMSE 不明确 |

每个区间来自 2,000 次 whole-condition paired bootstrap。它不会创造 2,000 个新电芯，也不是
预注册的因果显著性检验。Primary 与 keep-last acquisition sensitivity 的六组方向和数值相同，
但 timestamp/retry 语义仍是 provisional。

## 这说明什么？

最重要的观察不是“EIS 有用”或“EIS 没用”，而是：EIS 的关联增量取决于 ageing mode 和 model
family。特别是在 cyclic 中，同一批配对数据上，HGB 与 ElasticNet 给出了相反方向。这可能来自
不同模型利用高维 EIS 特征的方式、样本支持、共线性或真实动力学差异，但本轮不能把这些假设写成
已证明原因。

## 这项工作还不能做什么？

它不能支持以下说法：

- 模型已经能预测故障、热失控、RUL 或换电时间；
- EIS 普遍提高预测，或已经证明因果作用；
- HGB / ElasticNet 是最终模型或 universal winner；
- 结果来自 protected、external 或 prospective test；
- 可以部署到生产、自动报警或估算成本收益。

下一步不是马上打开 protected test，而是先解释 mode × model interaction、检查特征稳定性和
失败案例，再用新的冻结协议决定是否值得进行外部或 protected evaluation。

## 数据和代码边界

- KIT result data：<https://publikationen.bibliothek.kit.edu/1000169196>
- Data descriptor：<https://doi.org/10.1038/s41597-024-03831-x>
- 本项目是基于公开数据的独立复现与扩展，不是 KIT、Argonne 或原作者项目。
- CALCE 备用方向已停止；当前只推进 KIT 主项目。
