<!--
RESEARCH-SEMINAR SOURCE — INTERNAL DEVELOPMENT DRAFT

Evidence boundary:
- Source of every project metric in this deck:
  artifacts/local/paired-runs/kit-f1-paired-b-dcore-dev-v2-20260802T201053-cdt/
- This paired development run completed with exit code 0 and artifact validation.
- It remains development-only: scientific_result=false, formal_model_result=false,
  protected_test_result=false, publication_ready=false, resume_approved=false,
  selection_performed=false.
- Do not describe these numbers as protected-test, final, production, publication-ready or resume-
  approved results.

Export status:
- A current 21-slide editable PPTX is generated with the repository build script.
- Run from the repository root:
  uv run --isolated scripts/build_research_seminar_pptx.py
- Output: docs/slides/battery-early-warning-research-seminar-kit-b-vs-dcore-development-oof.pptx
- Same-layout PNG previews are written to
  docs/slides/rendered-kit-b-vs-dcore-development-oof/ for visual QA.
- Speaker notes are copied into the PowerPoint Notes pane by python-pptx.
- The prior 22-slide export is retained under docs/slides/archive/ as a legacy artifact.
-->

# 电池衰减预警：EIS 能让预测更好吗？

## KIT 228 电芯的 B vs D_CORE/EIS 配对 development OOF 研究

**当前结论：完成 B 与 D_CORE/EIS 的配对开发运行；尚无 protected test、模型选择或生产结论**

Lu Zhang — independent public-data research project

<!--
Speaker notes:
今天讲的不是“模型已经可以换电池”，而是一个更窄、更可审查的问题：同一批快照、同一外层
拆分、同一模型下，加入 D_CORE EIS 特征是否改变未来 H3 SoH loss 的预测误差。所有数字都来自
同一个受控 development run；不能称为最终模型成绩。
-->

---

## 1. 研究目的：不是测到 SoH，而是预测未来变化

```text
当前检查                         运维真正想提前知道的事
容量 -> 当前 SoH        ->      未来三次检查后会下降多少？
历史 + 使用条件         ->      哪些电芯值得优先复测或人工审查？
```

- 当前容量和 SoH 是**测量结果**，不是未来预测。
- 本项目预测连续的未来衰减量，再派生未来 SoH。
- 它不预测热失控、火灾、内短路或所有故障。
- “优先复测”是业务价值假设，不是已验证的维护策略。

<!--
Speaker notes:
一个电池现在 92% SoH，并不能直接告诉我们它未来三次检查会掉 0.5 个百分点还是 8 个百分点。
本项目先回答“会掉多少”，而不是先造一个 yes/no 告警。
-->

---

## 2. 官方 KIT 数据：独立单位不是频率行

**KIT battery ageing result data v2，CC BY 4.0**

```text
76 个 operating conditions × 3 个 physical replicate cells = 228 cells
```

| 层级 | 数据含义 | 能否随机拆分？ |
|---|---|---|
| Condition | 一组固定老化条件 | **不能拆散** |
| Cell | 一个真实 18650 电芯 | 同 condition 的三个 cell 一起 hold out |
| Check-up | 同一电芯的重复诊断 | 不是独立电芯 |
| Spectrum / frequency row | 一次阻抗检查的细节 | 不是独立样本 |

官方来源：<https://publikationen.bibliothek.kit.edu/1000169196>
数据 DOI：<https://doi.org/10.35097/1969>

<!--
Speaker notes:
数据文件可以有上百万行，但科学上的独立规模仍主要由 76 个 conditions 和 228 个 cells 决定。
如果随机拆频率行或 check-up，同一个电芯会同时出现在训练和测试中，结果会过度乐观。
-->

---

## 3. 本次配对 H3 训练支持：三种老化模式不能混为一个“cycle number”

下表是**本次 B vs D_CORE 配对 H3 run 实际纳入**的 condition / cell 支持，不是完整 76 条件
inventory 的模式配额。

| 模式 | 本次支持的条件数 / 电芯数 | 电芯经历什么 | 主要 exposure |
|---|---:|---|---|
| Calendar | 16 / 48 | 在目标电压或 SoC 下静置 | elapsed days |
| Cyclic | 35 / 99 | 受控充放电循环 | EFC + elapsed days |
| Driving profile | 11 / 33 | WLTP-derived 动态功率需求后充电 | EFC；距离仅在可追溯时使用 |

- 三种模式分别训练和报告。
- `check-up index` 表示顺序，不是物理时间。
- 模式之间的误差不能直接混成一个“全局最好模型”。

<!--
Speaker notes:
Calendar、cyclic 和 driving profile 对应不同 exposure 过程。即使都用 H3，三次检查跨过的实际天数
或 EFC 也不同，因此必须按模式解释。
-->

---

## 4. Ground truth：Q0、当前 SoH 与 H3 future loss

官方 result data 包含容量、EIS 和 pulse 等结果；本次以容量序列 B 为主输入，并和加上 EIS 的 D_CORE
做配对诊断：

```text
Q0 + 当前/过去 SoH + 过去衰减斜率
+ check-up 顺序 + elapsed days + normalized throughput
+ ageing temperature/rates/profile
-> 预测 H3 SoH loss
```

- 使用 `cell_eocv2.zip` 与 `cell_eisv2.zip`；数据绑定 SHA-256 记录在 run manifest。
- 3,999 标准化容量 endpoints；3,993 个可用 EIS acquisitions。
- 2,919 个 eligible B/D 配对快照进入固定 F1 H3 continuous regression。
- pulse、O2 classification、RUL 未在本 run 中训练。

<!--
Speaker notes:
必须把“数据集里有 EIS”和“每个模型使用了 EIS”分开。B 是容量/条件基线；D_CORE 才加入在当时
可获得、经汇总后的 EIS 核心特征。配对结果只能回答这次 development diagnostics，不能发布结论。
-->

---

## 5. EDA：3,999 endpoints 怎样变成 2,919 paired snapshots？

```text
SoH_t (%) = 100 × Capacity_t / Q0

soh_loss_pp_h3 = SoH_t - SoH_t+3

predicted_future_soh = current_soh - predicted_loss
```

- `Q0`：每个电芯冻结的首个有效标准容量 endpoint。
- `H3`：未来第三次**完成的** check-up，不是固定三天或三次 cycle。
- 单位：SoH percentage points（pp）。
- 负 loss 保留；不偷偷裁剪为 0。
- 没有 exact H3 的末端记录标为 `no_h3_followup`，不是负样本。

<!--
Speaker notes:
Ground truth 来自未来真实容量检查，不是人工标签。模型预测的是下降量；未来 SoH 只是从当前
SoH 减去预测下降量。这样能同时看到现在状态和未来变化。
-->

---

## 6. Feature engineering：B baseline 与 D_CORE/EIS

| 状态 | 数量 | 含义 |
|---|---:|---|
| `eligible_f1_b` | **2,919** | 有足够历史，也有 exact H3 truth |
| `insufficient_history` | 456 | 前两个 check-up 不足以构造历史特征 |
| `no_h3_followup` | 624 | 数据右边界没有未来第三次检查 |
| 合计 | **3,999** | 与 source manifest 对齐 |

可训练快照按模式：calendar 1,134；cyclic 1,190；profile 595。

**关键原则：**缺失原因分别记录，不能统一填成 0 或丢掉不解释。

<!--
Speaker notes:
2,919 不是 2,919 个独立电池，而是 180 个满足当前 B-view 历史/H3 条件的电芯在多个时间点的
快照。condition-held-out split 才能处理这些相关记录。
-->

---

## 7. Preprocessing、feature engineering 与 no leakage

固定 B-view 输入包括：

1. 当前、上一、上上次 SoH；
2. 最近一次和两个间隔的 signed loss；
3. check-up index、从 Q0 起的 elapsed days；
4. 按 Q0 归一化的累计 throughput；
5. ageing temperature、charge rate、discharge rate、profile。

所有特征都只能使用预测时点 `t` 及以前的数据。

- 不读 `t+1...t+3` 的 future values；
- preprocessing 只在 outer-training conditions 内拟合；
- feature validity、target arithmetic 和 identity/count 都由运行时校验。

<!--
Speaker notes:
prefix-only 的意思是：在历史回放到 check-up t 时，输入只能来自当时已经知道的信息。未来的
H3 容量只在 label/evaluation 侧使用。
-->

---

## 8. 数据怎么分：完整 condition holdout

```text
每种 ageing mode
  -> 3 个 outer condition-group folds
       -> held-out condition 的 3 个 cells 和全部 check-ups 都不进训练
       -> 训练条件拟合 preprocessing + ElasticNet/HGB
       -> held-out condition 产生 OOF prediction
```

- 禁止 random row / random check-up / fragmented-cell split。
- calendar、cyclic、profile 分开建 fold。
- 这是 development OOF：模拟“新的 operating condition”，不是 protected external test。

<!--
Speaker notes:
这种切法比随机行拆分更难，但更接近真正的问题：模型能否面对训练时没见过的一整组老化条件。
同一个 condition 的三个 replicates 不能跨到不同 folds。
-->

---

## 9. Variable selection：先不做 outcome-driven 筛选

本 milestone 的变量由科学含义和 leakage 规则预先固定：

- 不在全数据上按 correlation 挑变量；
- 不根据 held-condition 结果删除“不好看”的变量；
- ElasticNet 用 regularization 做受控 shrinkage；
- HGB 用浅树、有限 leaf nodes 和固定 complexity；
- 没有 hyperparameter search，也没有 ensemble。

**原因：**每个 ageing mode 的可用独立 conditions 有限。小数据上反复筛选和调参，很容易把偶然模式当作规律。

<!--
Speaker notes:
这里不是说 variable selection 永远没用，而是当前 run 的目标是一个严格、可重复的第一基线。
未来如要筛变量，需要在 condition-grouped inner CV 中重新冻结方案。
-->

---

## 10. 为什么是两条 baseline + 两个模型？

| 方法 | 直白解释 | 作用 |
|---|---|---|
| Persistence | 预测未来不下降 | 最低门槛 |
| Recent slope | 最近一次下降速度 × 3 | 强而透明的趋势基线 |
| ElasticNet | 正则化线性回归 | 看固定输入的近似线性组合够不够 |
| HGB | 受约束的树模型 | 表达非线性和变量交互 |

- 参数在运行前固定。
- B：每种 mode 3 folds × 2 fitted models；D_CORE：同一组 3 folds × 2 fitted models。
- 三种 mode 合计 **54 fits**。
- Baselines 直接计算，不计入 fitted-model 次数。

<!--
Speaker notes:
这不是模型大赛。Recent slope 是必须认真对待的业务基线：如果复杂模型连它都不能稳定超过，
就没有理由增加部署复杂度。
-->

---

## 11. Training + review gates：结果先停在 development

```text
2,919 paired snapshots
× B 的 4 families + D_CORE 的 2 fitted families
= 23,352 OOF prediction rows
```

运行记录：

- run：`kit-f1-paired-b-dcore-dev-v2-20260802T201053-cdt`
- planned/completed fits：54 / 54
- artifact validator：pass；paired bootstrap draws：2,000
- source、snapshot、fold、prediction 和 summary 都有 SHA-256 绑定
- O2 fits/predictions：0；D_CORE/EIS 是 paired diagnostic，not promotion.

<!--
Speaker notes:
validated 表示文件结构、数量、hash 和运行契约完成校验，不等于科学发表或生产批准。最终 manifest
仍明确写着 publication_ready=false 和 protected_test_result=false。
-->

---

## 12. 怎样评估：condition-balanced MAE 为主

Primary：

```text
先在每个 held-out condition 计算 MAE
-> 再让每个 condition 等权平均
```

Secondary：median absolute error、RMSE、signed bias、R²。

- MAE 单位是 SoH percentage points。
- 条件等权避免 check-up 多的 condition 主导总体分数。
- R² 与 MAE 可能给出不同排序；不能只挑一个好看的数字。
- 本运行没有 per-prediction interval。

<!--
Speaker notes:
例如 cyclic 中 ElasticNet 的 R² 比 recent slope 高，但 recent slope 的 MAE 更低。协议规定 MAE 是
primary，因此不能事后改用 R² 选模型。
-->

---

## 13. Calendar：B 与 D_CORE/EIS 几乎相同

| View / 方法 | MAE (pp) | Median AE (pp) | RMSE (pp) | R² |
|---|---:|---:|---:|---:|
| B / ElasticNet | 0.209 | 0.121 | 0.362 | 0.766 |
| D_CORE / ElasticNet | 0.260 | 0.124 | 0.485 | 0.580 |
| B / HGB | 0.139 | 0.058 | 0.284 | 0.856 |
| D_CORE / HGB | **0.138** | 0.059 | **0.283** | **0.857** |

Support：16 conditions、48 cells、1,134 snapshots。

**配对差异：**ElasticNet `D_CORE − B = +0.051 pp`，95% CI `[-0.020, +0.144]`；HGB `−0.001 pp`，95% CI `[-0.013, +0.009]`。

<!--
Speaker notes:
Calendar 中 D_CORE 没有清楚、可分辨的增量价值；微小数字不应被讲成机理或产品收益。它仍没有
protected test，也不能推导成真实车队部署性能。
-->

---

## 14. Cyclic：EIS 对 HGB 有诊断性改善，对 ElasticNet 变差

### 同一快照、fold、权重的配对 condition-balanced MAE（pp，越低越好）

| 模型 | B | D_CORE | D_CORE − B | 95% paired bootstrap CI |
|---|---:|---:|---:|---|
| ElasticNet | 5.113 | 5.918 | +0.804 | [+0.285, +1.361] |
| HGB | 5.809 | 5.184 | **−0.625** | [−1.056, −0.241] |

Support：

- cyclic：35 eligible conditions、99 eligible cells、1,190 snapshots；
- D_CORE 只比较两种 fitted models；capacity baselines仍是解释基准，不是 paired EIS claim。

<!--
Speaker notes:
在 cyclic 中，同一 EIS 表示对 HGB 的 MAE 有诊断性改善，却使 ElasticNet 明显变差。这不是可
以推广的“EIS 总是更好”，而是下一轮需要解释的模型依赖结果。
-->

---

## 15. Driving profile：结果依赖模型，证据有限

| 模型 | B MAE | D_CORE MAE | D_CORE − B | 95% paired bootstrap CI |
|---|---:|---:|---:|---|
| ElasticNet | 4.791 | 4.462 | −0.330 | [−1.208, +0.433] |
| HGB | 4.239 | 4.453 | +0.215 | [+0.036, +0.384] |

Profile support：11 eligible conditions、33 cells、595 matched snapshots。

1. 一个 CI 跨 0，另一个变差；不能选择看起来有利的那一个。
2. Profile 独立 conditions 很少，估计本来就更不稳定。
3. 固定 H3 不等于固定物理跨度：三次 check-up 跨过的 days/EFC 不一致。
4. `selection_performed=false`；没有 champion 或 ensemble。

**结论：**按模式描述 development OOF，不把一个局部低分包装成全局模型。

<!--
Speaker notes:
“没有全局 EIS 赢家”不是含糊其辞，而是这组配对结果本身。下一步应先确认时间/温度语义和
跨数据集可重复性，而不是继续堆更复杂模型。
-->

---

## 16. EIS mode/model 依赖：不能写成“EIS 总是更好”

- 不是 protected test，也没有 external dataset。
- 不是 publication-ready 或 resume-approved 指标。
- 没有 O2 快速衰减分类、故障 yes/no 或 maintenance threshold。
- 没有 pulse/auxiliary diagnostics、RUL、per-prediction interval 或 protected test。
- 没有证明模型能预测安全事故、真实车队故障或生产收益。
- 没有模型选择：最低 development MAE 不等于最终 champion。

所有正式运行 artifact 都保留：
`scientific_result=false`、`formal_model_result=false`、`publication_ready=false`。

<!--
Speaker notes:
这页是材料可信度的核心。EIS 已进入 D_CORE 配对诊断，但不能把局部 development delta 写成
“EIS 总是改善预测”。也不能把 development OOF 数字直接放进简历或对外文章。
-->

---

## 17. Fleet simulation UI：把预测过程讲清楚

```text
228-cell roster -> 选择 calendar/cyclic/profile -> 沿 check-up 回放
      -> 当前 SoH + 历史证据 -> H3 prediction -> truth 到期后再解锁
```

- `insufficient_history` 与 `no_h3_followup` 分开显示；
- 预测、实测 truth 和不可用状态使用不同视觉编码；
- fleet replay 应并排显示 hash-bound B 与 D_CORE development OOF，并永久标注证据等级；
- Final / protected-test 模式保持禁用，不能把 development replay 冒充最终模型 demo。

<!--
Speaker notes:
UI 不是 battery digital twin。它的价值是让用户看到“当时知道什么、模型预测什么、什么时候才
知道答案”，并保留数据不可用原因。
-->

---

## 18. Business / production impact：从预警到人工决策

```text
scheduled diagnostic check-up
 -> future-loss estimate + evidence packet
 -> prioritize retest / engineering review
 -> human decides monitoring or service action
```

潜在价值（尚未验证）：

- 把工程师注意力优先放在快速衰减或证据异常的电芯；
- 同时显示 current health、future estimate、数据质量和模型来源；
- 记录每次复测、审查与人工决策；
- 用 missed cases、review burden、lead time 和成本做 prospective evaluation。

<!--
Speaker notes:
模型不直接换电池。合理的第一步是“复测和人工审查排序”。是否省钱、是否减少漏检，都要在真实
流程中另外测量。
-->

---

## 19. Limitations + next protected / external test

1. **错误分析：**找出 cyclic/profile 中 recent slope 优于 fitted models 的 conditions。
2. **Horizon sensitivity：**在新 protocol 下比较 H1/H3/H5，同时报告真实 days/EFC。
3. **EIS incremental study：**同 snapshot、同 fold、同 model family 比较 B 与 D_CORE。
4. **变量稳定性：**只在 condition-grouped inner CV 中做 selection/stability 分析。
5. **外部验证：**冻结模型后，申请一次 protected evaluation；再寻找跨数据集复现。
6. **O2/noise：**取得 capacity technical repeats 后再决定是否建立快速衰减概率。

<!--
Speaker notes:
下一步不是盲目增加神经网络。先解释失败模式，再检验 EIS 是否真正增加信息，最后才讨论选择、
告警和部署。
-->

---

## 20. 结论

**这次完成了：**

- 228 cells / 76 conditions 的可追溯容量序列 inventory；
- exact-H3 continuous target 与 prefix-only B view；
- condition-held-out、按模式分开的 54-fit paired development run；
- 2,919 matched snapshots、23,352 OOF prediction rows 和完整 hash lineage；
- 一个关键观察：calendar 与 cyclic/profile 没有相同的描述性最佳方法。

**仍未完成：**protected test、模型选择、EIS 增量、O2 告警、生产影响和公开发布批准。

> 可靠的研究结论，不是永远找到一个更复杂的赢家；有时是知道简单方法在哪里更强。

<!--
Speaker notes:
项目现在已经从“纸上计划”进入了真实训练和可审计结果阶段，但还没有跨过最终验证边界。下一轮
工作的重点是解释模式差异、加入严格配对的 EIS 对照，并保留 protected test。
-->
