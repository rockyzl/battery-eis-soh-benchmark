# KIT D_CORE EIS 配对科学就绪审计

日期：2026-08-02
结论：**GO——允许进行一次最小、受控、development-only 的 B / D_CORE 配对 OOF 运行**。
边界：这不是模型胜出、正式测试、生产部署或公开科研结论的授权；O2 仍然禁止训练。

## 大白话结论

KIT 的 EIS 数据可以和当前 check-up 的容量快照安全配对。原因不是“时间看起来差不多”，而是官方流程明确规定：每次 check-up 先完成容量测试，再进行 RT EIS；文件名还能把容量和 EIS 锁定到同一块物理电芯。

真正用于 H3 预测的 2,919 个快照，全部都找到了同一次 check-up 的有效 RT、50% SoC EIS。B 和 D_CORE 因而可以在完全相同的快照、标签、fold 和权重上比较。

## 官方来源与完整性

- 官方结果数据：KIT RADAR DOI `10.35097/1969`，bag date `2024-09-27`。
- `cell_eocv2.zip`
  - bytes: `93,167,507`
  - MD5: `b4f42914c8eaf176fd47e33e6f112d7c`，与官方 bag manifest 一致
  - SHA-256: `95de03af7d73efdeed97fcd13fbdca2eeabe85b2971b08394b665c8639b3e140`
- `cell_eisv2.zip`
  - bytes: `19,894,633`
  - MD5: `6eb09ae51c8a9e9b4b02b33e739787ab`，与官方 bag manifest 一致
  - SHA-256: `bc1c56e05f50a41ed9d9f752f65fc7833b49ad6bc369f67ccfc14288a9815fed`
- 语义证据：官方 `Data Structure v05.3.xlsx` 和 `Readme v2.pdf`。前者定义 `CHECKUP_RT` 为先测容量、随后在 RT 测 EIS/pulse；EIS 在充电路径按 10/30/50/70/90% SoC 进行。

## 冻结配对规则

1. 物理电芯由文件名中的 `condition + replicate + slave + channel` 唯一识别。
2. 容量快照只采用 EOCV2 中 check-up、放电完成的有效容量 endpoint；同一电芯按完成的 `num_cycles_checkup` / 时间顺序排列。
3. 当前 EIS 只采用 `valid=1`、`cyc_charged=1`、`is_rt=1`、`soc_nom=50` 的完整 spectrum。
4. EISV2 没有显式 check-up counter。因此利用官方 check-up 顺序，把 EIS 配给同一电芯中时间上最近、且已先完成的容量 endpoint；同时强制 EIS 必须早于下一次容量 endpoint。
5. 同一 endpoint 有多次 EIS 时不把它们当成多个训练样本：主分析逐频率做 finite mean；`keep-last` 只作为预注册敏感性分析。
6. H3 是当前 endpoint 之后第三个**实际完成并存在于数据中的**容量 endpoint，而不是机械要求 counter 数字等于 `current+3`。
7. 模型的可用时点必须写成“当前 check-up 的 RT50 EIS 完成以后”。不能把更早的容量 endpoint timestamp 冒充最终预测时点。
8. 所有 EIS acquisition 必须严格早于其 H3 endpoint；任何违反都 fail closed。

## 实际配对数字

| 项目 | 数量 |
|---|---:|
| 物理电芯 | 228 |
| 实验条件 | 76 |
| 容量 endpoints | 3,999 |
| 全部 EIS spectra | 40,615 |
| charged + RT + 50% SoC spectra | 3,998 |
| 再要求 `valid=1` | 3,993 |
| B 的 H3 可训练快照 | 2,919 |
| D_CORE 精确匹配快照 | 2,919 |
| B 中因 EIS 缺失而不能进入 D_CORE 的 H3 快照 | 0 |

按老化模式：

| 模式 | 全部 endpoints | B H3 | D_CORE matched | cells | conditions |
|---|---:|---:|---:|---:|---:|
| calendar | 1,374 | 1,134 | 1,134 | 48 | 16 |
| cyclic | 1,850 | 1,190 | 1,190 | 99 | 35 |
| profile | 775 | 595 | 595 | 33 | 11 |
| 合计 | 3,999 | 2,919 | 2,919 | 180 | 62 |

其余 48 个电芯不是“被模型删除”，而是没有同时满足两次历史 check-up 和完整 H3 future endpoint。

## 歧义、重复与泄漏检查

- 3,993 个有效 RT50 spectra 全部有同电芯 prior capacity candidate。
- 没有任何 spectrum 穿过下一次容量 endpoint；future leakage violations = `0`。
- 最后一次当前 EIS completion 晚于或等于 H3 endpoint 的情况 = `0`。
- exact duplicate spectrum keys = `0`。
- 3,999 个 endpoint 中：3,963 个有一次有效 RT50 EIS，15 个有两次，21 个没有。
- 60 小时 retry flag 共 15 个；它只标记，不删除或另造训练样本。
- 全部 3,993 个所用 EIS 的 `timestamp_origin=1`，即官方定义的 exact timestamp origin。
- `timestamp` 与 `timestamp+duration` 两种完成时间解释，只造成 1 次 endpoint assignment 差异：`P073_3_S14_C01` 的尾部异常记录。该电芯缺少 counter 12，counter 13 的 EOC duration 异常长。这个差异不改变任何作为**当前输入**的 2,919 个 D_CORE H3 快照，因此不阻断本次配对 development run；必须在报告中保留为数据异常。
- 3 个 `is_rt=1` spectrum 的实际平均温度约 1–2°C，但全部位于没有 H3 的尾部，不进入 2,919 个训练快照。其余 RT 温度偏差仍应作为 EIS 可比性的限制报告。

## EIS 特征与缺失

- 每个原始 spectrum 严格包含 sequence 0–28，共 29 个固定、严格降序频率。
- sequence 0 的 compensated 值全缺失；sequence 13 缺失较多。冻结的 D_CORE 排除 0 和 13。
- D_CORE 使用其余 27 个频率的 compensated real/imaginary，共 54 个 EIS 数值，再加 B 特征。
- 2,919 个 D_CORE 行中，82 行至少有一个剩余 EIS 值缺失；主要出现在 sequence 25–28，另有 1 行 sequence 1 缺失。
- 现有实现只用 outer-training partition 的 weighted median 做填补，并只根据 training missingness 创建 indicator；不会从 held condition 学预处理参数。
- `soh_imp`、`z_ref_init`、`z_ref_now` 不进入特征，避免直接使用源数据健康标签或派生 shortcut。

## Fold 安全

现有 outer 3-fold / inner 2-fold 使用完整 condition 分组。实际 outer folds 中：

- calendar：每折 validation 354–420 个快照；
- cyclic：每折 validation 396–397 个快照；
- profile：每折 validation 189–207 个快照；
- 每一折 condition overlap = `0`；
- 每一折 physical-cell overlap = `0`；
- D_CORE 在每折的 train/validation 中都不为空。

## 对现有 paired engine 的独立判断

通过：

- 一次 check-up 只生成一个 snapshot；频率行和 retry 不扩增样本权重。
- B 保留自己的完整 H3 cohort；D_CORE 只取匹配交集。
- paired delta 重新在 D_CORE 交集上提取同一模型家族的 B 与 D_CORE prediction，要求 snapshot IDs 完全一致。
- fold-local imputation/scaling、condition-held-out folds、whole-condition paired bootstrap 均符合冻结合同。
- 现有测试覆盖 29-frequency contract、0/13 排除、retry aggregation/keep-last、配对 identity、fold preprocessing 和 O2 zero-fit lock。

必须保留的限制：

- artifact 中的 `snapshot.timestamp_s` 是容量 endpoint 时间，不等于所有 EIS 已完成的预测可用时间。paired run/后续 UI 必须使用 acquisition lineage 表达真实 availability，不能作实时提前量声明。
- D_CORE 当前未把实际 `t_avg_degC` 作为输入；因此 EIS 增量可能混有少量测温差异，不能解释成纯粹的电化学机理收益。
- 本次只允许 development OOF 对比；没有 champion、threshold、RUL、failure classifier 或生产告警。

## 最小授权运行建议

1. 冻结上述两个官方 archive 的 SHA-256、现有 v2 config/schema/code/tests hashes。
2. 冻结 exact 2,919 snapshot IDs、62 condition groups、三个 mode 的 folds，以及 acquisition lineage。
3. 明确记录 prediction availability 为当前 check-up 最后一个 aligned RT50 EIS 完成以后。
4. 运行预注册 B 和 D_CORE：persistence、recent-slope、ElasticNet、HGB；O2 为零 fits。
5. 对 repeated EIS 同时报告 equal-finite-mean 主结果和 keep-last sensitivity。
6. 只报告同模型家族、同 2,919 行的 condition-balanced MAE/RMSE/bias/R² 和 whole-condition paired bootstrap。
7. 独立复核 artifacts 后，才可称为 accepted development OOF；仍不能称正式测试或公开结论。

机器可读证据位于忽略目录：`artifacts/audits/kit-d-core-eis-readiness.json`。
