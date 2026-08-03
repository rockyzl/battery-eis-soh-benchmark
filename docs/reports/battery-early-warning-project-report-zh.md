# KIT 电池衰减预警项目报告（paired development OOF）

日期：2026-08-02
权威 run：`artifacts/local/paired-runs/kit-f1-paired-b-dcore-dev-v2-20260802T201053-cdt`
可提交派生来源：`demo/fleet-replay/data/kit-paired-development-summary.json`
状态：**内部 development 结果；不是 protected test、正式发表结果或生产模型**

```text
scientific_result=false
formal_model_result=false
protected_test_result=false
publication_ready=false
selection_performed=false
resume_approved=false
deployment_ready=false
```

## 1. 一句话说明这个项目

模型在每次电池 check-up 时，根据当时已经知道的信息预测：

> 未来第三次检查时，SoH 会再下降多少个百分点？

然后用严格配对的开发回放回答第二个问题：

> 在容量历史和工况之外加入 EIS，是否让同一个模型的预测更准确？

本轮不是“会坏 / 不会坏”的分类，也没有预测热失控或突然失效。

## 2. 数据来源和结构

数据来自公开 KIT battery ageing result data v2：

- 228 个 commercial LG INR18650HG2 NMC/C+SiO cells；
- 76 个 operating conditions，每个 condition 三个 physical replicates；
- 16 calendar、48 cyclic、12 driving-profile conditions；
- 重复 diagnostic check-ups，包含 capacity、EIS 和 pulse measurements；
- 温度条件包含 0、10、25 和 40 °C。

来源：

- KITopen：<https://publikationen.bibliothek.kit.edu/1000169196>
- Dataset DOI：<https://doi.org/10.35097/1969>
- Descriptor：<https://doi.org/10.1038/s41597-024-03831-x>

层级关系：

```text
condition
  -> 3 physical cells
      -> repeated check-ups
          -> one standardized capacity endpoint
          -> EIS acquisitions
              -> spectrum frequency rows
```

频率行不是独立电池。训练/验证以完整 condition 分组，避免 replicate cells 和重复 check-ups 跨边界。

## 3. 三种老化模式

| Mode | 官方 conditions / cells | 过程 | 合理 exposure |
|---|---:|---|---|
| Calendar | 16 / 48 | 在固定 voltage/SoC 下静置 | elapsed days |
| Cyclic | 48 / 144 | 受控充放电 | equivalent full cycles + days |
| Driving profile | 12 / 36 | WLTP-derived 动态负载 | EFC；有来源时才用 distance |

三种 mode 分别训练和报告，不能把所有 snapshot 混成一个总分。

## 4. Ground truth：Q0、SoH 和 H3

每个电芯冻结初始容量 `Q0`：

```text
SoH_t (%) = 100 × Capacity_t / Q0
soh_loss_pp_h3 = SoH_t − SoH_t+3
predicted_future_soh = SoH_t − predicted_loss
```

`t+3` 是未来第三个 completed check-up。它不是固定三天、三周或三个 cycles。没有第三个未来
check-up 的记录是 `no_h3_followup`，不能填成 zero loss。

## 5. EDA 与 paired support

权威派生 summary 记录：

| 项目 | 数量 |
|---|---:|
| Real cells | 228 |
| Conditions | 76 |
| Capacity endpoints | 3,999 |
| Valid RT-50 EIS spectra / acquisitions | 3,993 |
| Paired eligible cells | 180 |
| Paired eligible snapshots | 2,919 |
| Insufficient-history snapshots | 456 |
| No-H3-follow-up snapshots | 624 |

按 mode 的 paired eligible support：

| Mode | Conditions | Cells | Snapshots |
|---|---:|---:|---:|
| Calendar | 16 | 48 | 1,134 |
| Cyclic | 35 | 99 | 1,190 |
| Driving profile | 11 | 33 | 595 |

2,919 是重复 check-up snapshots，不是 2,919 个独立电芯；主要不确定性必须按 condition 处理。

## 6. 两个输入视图

### B：不含 EIS 的强基线

固定 12 个输入：当前、前一次和前两次 SoH；最近一次和两个间隔的 signed loss；check-up index；
elapsed days；Q0-normalized cumulative throughput；ageing temperature；charge/discharge rate；profile。

### D_CORE：B + 当前检查点 EIS

D_CORE 在相同 B 输入上加入完成时间与当前 capacity endpoint 匹配的 RT-50 EIS 特征。比较始终使用：

- 相同 snapshot IDs；
- 相同 outer folds；
- 相同 sample weights；
- 相同模型家族；
- 相同 target。

这减少了样本和 split 差异，但仍然是观察性 development comparison，不是 EIS 的因果实验。

## 7. Preprocessing、feature engineering 和 variable selection

所有 learned preprocessing 只能在 outer-training conditions 内拟合。D_CORE 的复数阻抗信息转换为
冻结的数值特征，并保留缺失指示；不存在的值不填成物理零。

本 milestone 不做 outcome-driven variable selection、hyperparameter search 或 ensemble：有效独立
condition 数远少于 snapshot 数，在全数据上按相关性挑变量会泄漏 held-condition outcome。
ElasticNet 的 regularization 仅提供受控 shrinkage，不能被宣传为经过稳定性证明的特征选择。

## 8. 模型与训练设计

| 方法 | View | 角色 |
|---|---|---|
| Persistence | B | 未来 loss = 0 的最低门槛 |
| Recent slope | B | 最近 signed loss × 3 的透明趋势基线 |
| ElasticNet | B / D_CORE | 正则化线性 candidate |
| HGB | B / D_CORE | 受约束的非线性 candidate |

每个 mode 使用 3-fold condition-grouped outer OOF。B 中两个 fitted families 各三折；D_CORE 在
primary 与 keep-last sensitivity policy 下各三折：

```text
3 modes × 3 folds × (2 B fits + 2 D_CORE primary + 2 D_CORE sensitivity) = 54 fits
```

所有 54 fits 完成，产生 23,352 OOF prediction rows。没有训练 O2，没有打开 protected test，
没有执行 selection 或 promotion。

ElasticNet 的 `max_iter=250000` 是在失败 lineage 后经 synthetic stress reproducer 审查的 numerical
convergence budget 修复；`alpha=0.01`、`l1_ratio=0.1`、tolerance、folds、features 和 metrics 均未
改变。它不是结果驱动的模型调参，也不证明更高迭代次数会改善泛化。

## 9. 评价方法

Primary metric 是 condition-balanced MAE：先对每个 held-out condition 计算 MAE，再让 conditions
等权平均。Secondary metrics 是 median AE、RMSE、signed bias 和 R²。

配对差值统一定义：

```text
delta = metric(D_CORE) − metric(B)
```

对于 MAE / RMSE，delta < 0 表示加入 EIS 后误差下降。每个 delta 使用 2,000 次 whole-condition
paired bootstrap 和 95% percentile interval。Bootstrap draws 不是新电芯，也不是因果检验。

## 10. B-view 简单基线

| Mode | Persistence MAE / RMSE / R² | Recent slope MAE / RMSE / R² |
|---|---|---|
| Calendar | 1.130 / 1.355 / -2.273 | 0.242 / 0.448 / 0.642 |
| Cyclic | 9.448 / 13.232 / -1.040 | 5.026 / 8.444 / 0.169 |
| Profile | 6.051 / 9.931 / -0.591 | 2.988 / 6.105 / 0.399 |

这些基线保留为参照；本轮没有选 champion。

## 11. Paired B vs D_CORE 结果

### 11.1 完整 primary 结果

| Mode | Family | B MAE / RMSE / R² | D_CORE MAE / RMSE / R² |
|---|---|---|---|
| Calendar | ElasticNet | 0.209 / 0.362 / 0.766 | 0.260 / 0.485 / 0.580 |
| Calendar | HGB | 0.139 / 0.284 / 0.856 | 0.138 / 0.283 / 0.857 |
| Cyclic | ElasticNet | 5.113 / 7.373 / 0.367 | 5.918 / 8.253 / 0.206 |
| Cyclic | HGB | 5.809 / 8.846 / 0.088 | 5.184 / 8.016 / 0.251 |
| Profile | ElasticNet | 4.791 / 6.962 / 0.218 | 4.461 / 6.609 / 0.295 |
| Profile | HGB | 4.239 / 7.435 / 0.108 | 4.453 / 7.416 / 0.113 |

### 11.2 Paired deltas 与区间

| Mode | Family | ΔMAE [95% CI] | ΔRMSE [95% CI] | 受限解释 |
|---|---|---|---|---|
| Calendar | ElasticNet | +0.051 [-0.020, +0.144] | +0.123 [-0.007, +0.279] | 点估计变差，方向不明确 |
| Calendar | HGB | -0.001 [-0.013, +0.009] | -0.001 [-0.032, +0.017] | 没有明确变化 |
| Cyclic | ElasticNet | +0.804 [+0.285, +1.361] | +0.880 [+0.221, +1.779] | 变差信号 |
| Cyclic | HGB | -0.625 [-1.056, -0.241] | -0.829 [-1.368, -0.329] | 改善信号 |
| Profile | ElasticNet | -0.330 [-1.208, +0.433] | -0.353 [-0.847, +0.260] | 点估计改善，方向不明确 |
| Profile | HGB | +0.215 [+0.036, +0.384] | -0.019 [-0.246, +0.210] | MAE 变差，RMSE 不明确 |

Keep-last sensitivity 的六组数值与 primary 相同，没有 sign reversal。只能说当前两种 acquisition
policy 没有改变这些开发结果，不能说 timestamp matching 已被证明正确。

## 12. 怎样理解“没有统一答案”？

- EIS 并非在所有 mode 和 model family 中都改善误差；
- cyclic 中，HGB 与 ElasticNet 给出相反方向；
- calendar 中 HGB 几乎不变，ElasticNet 点估计较差但区间跨 0；
- profile 的结论混合，且只有 11 个 eligible conditions；
- R² 是辅助指标，不能用一个好看的 R² 覆盖 primary MAE；
- 可能机制包括共线性、样本支持、非线性利用方式和老化动力学差异，但都尚未被证明。

因此正确句子是：

> 在这次 condition-held-out development OOF 配对比较中，EIS 的关联增量依赖 ageing mode 与
> model family；当前没有 universal winner，也没有因果结论。

## 13. 第一次失败 run 与修复

第一次 paired lineage 在 42/54 fits 后失败，目录保持不可变，没有续跑、补写或选择部分结果。
诊断发现失败点为 profile / primary / D_CORE / ElasticNet outer-0；real-data 只读审计显示输入有限但
高度病态，synthetic reproducer 也能在旧 iteration budget 下复现 convergence exhaustion。

修复只将 ElasticNet numerical `max_iter` 从 5,000 增至 250,000，并增加 sanitized error code；
所有科学身份保持不变。新 run 使用新目录和新授权从头执行。该证据支持当前工程机制，但不应夸大
为对任何 ElasticNet 失败都成立的普遍根因。

## 14. UI 和交付物

交互式 UI 保留：

- 228-cell fleet replay；
- 180 paired-eligible cells；
- calendar / cyclic / profile tabs；
- HGB / ElasticNet family switch；
- selected-cell B 与 D_CORE forecasts；
- prediction maturation 后的历史 truth 核对；
- 六组 aggregate metric/delta/CI cards；
- research-review queue 和缺失原因。

Commit-safe derived files 只包含浏览器需要的 projection、aggregates、flags 和 source hashes；不包含
raw KIT data、local grant 或 receipt。

## 15. 可能的业务价值与当前边界

长期上，一个通过外部验证的系统可能帮助工程师优先安排复测、解释不同诊断信号并减少无效检查。
本轮只证明了一套可追溯的开发比较流程。没有 prospective deployment、决策阈值、成本模型或真实
维护 action study，因此不能量化节省、可靠性提升或安全收益。

## 16. 下一步

1. 解释 mode × family interaction，检查共线性、特征稳定性和 failure cases；
2. 复核 timestamp/retry 语义，不能用 sensitivity equality 代替 source semantics；
3. 冻结下一轮分析问题，避免结果出来后改 metric 或 narrative；
4. 如需 protected/external evaluation，建立新的独立 approval 和一次性 label gate；
5. publication review 完成前保持全部 publication/deployment flags 为 false。

## 17. 项目范围

KIT 是唯一活动主项目。CALCE 备用方向已停止，不下载、不训练，也不是 runtime/config dependency。
Cambridge benchmark 是独立历史 lineage，不能与 KIT 结果混合。
