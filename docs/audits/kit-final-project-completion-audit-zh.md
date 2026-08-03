# KIT 主项目最终完成度审计

> **SUPERSEDED / HISTORICAL — 不代表当前状态。** 本文审计发生在 paired B/D_CORE 完成之前，
> 正文中的 B-only / EIS 未运行结论仅保留作历史。当前权威状态是：development OOF 已完成
> 54/54 fits、2,919 个相同 eligible snapshots 和 23,352 条 OOF predictions；O2=0，且没有
> selection、protected test、publication 或 deployment。结果不能证明统一赢家或 EIS 因果
> 效应。KIT 是唯一活动项目；CALCE 已停止，Cambridge 仅为冻结历史材料。请以
> `docs/live-early-warning-tasks.md` 为准。

审计日期：2026-08-02
审计范围：只审计 KIT 主项目；CALCE/Cambridge 等备用 lineage 不计入当前交付
当前分支：`protocol/soh-target-v2`
当前 HEAD：`57f98bd` (`Define SoH-primary training protocol v2`)
审计方式：只读核验代码、正式运行、独立复核、UI、文档、幻灯片、测试与 Git 状态；本审计不修改科学运行 artifact

## 一句话结论

**总体结论：FAIL——KIT 主项目还不能被称为“全部完成”。**

但必须把两个层级分开：

| 层级 | 结论 | 大白话解释 |
|---|---|---|
| B-only development milestone | **PASS WITH LIMITATIONS** | 已经用 228 个真实 KIT 电芯完成 18/18 个拟合身份和 condition-held-out OOF，并通过独立复算；这是一个真实、受限的开发结果。 |
| 原始旗舰研究问题：`B` 对比 `D_CORE = B + EIS` | **FAIL / 未完成** | 本轮明确没有使用 EIS，也没有回答“EIS 是否在容量历史之外增加预测价值”。 |
| 最终/受保护/外部验证 | **FAIL / 未开始** | 没有 protected test、外部数据验证、最终模型选择或 publication approval。 |
| 可提交、可复现、可公开的完整项目包 | **FAIL** | 文档仍有矛盾，广义 mypy 失败，PPTX 过期且无 notes，UI 没有独立浏览器/可访问性 QA，Git 工作树未整理且没有 remote。 |

因此，当前最准确的表述是：

> KIT 主项目已经完成真实数据的 B-only development OOF 里程碑，但完整的 EIS 增量研究、最终验证和发布交付尚未完成。

## 审计结果总表

| 审计项 | 结果 | 核心证据/问题 |
|---|---|---|
| 官方 KIT 数据来源与许可 | PASS | KITopen、dataset DOI、Scientific Data 论文和官方 scripts 均有记录；`cell_eocv2.zip` SHA-256 与 source manifest 一致；CC BY 4.0。 |
| 228-cell / 76-condition roster | PASS | 228 个真实电芯、76 个条件、每条件 3 个物理重复；本轮 3,999 个 capacity endpoints。 |
| F1 exact-H3 target | PASS | `F1 = SoH_t - SoH_t+3`；末端无 H3 的记录不填零，负值保留。 |
| Condition-safe split | PASS | 每种 ageing mode 使用 complete-condition-held-out 3-fold OOF；同一 condition 的三个电芯不会跨 train/validation。 |
| B-only 真实训练 | PASS | 18/18 planned fits 完成；2,919 eligible snapshots；11,676 OOF rows。 |
| 指标独立复算 | PASS | 独立 review 重新计算全部主要/次要指标，最大绝对差 `1.78e-15`。 |
| Protected/EIS/O2 边界 | PASS | `selection_performed=false`、`protected_test_result=false`、`publication_ready=false`；没有读取 EIS 训练、没有 O2、没有 RUL/维修结论。 |
| 完整 B-vs-D_CORE 研究 | FAIL | `D_CORE` 尚未获得 EIS/capacity completion/alignment 语义批准；未构造 paired snapshots，未训练。 |
| UI 数据与交互静态契约 | PASS | 228 cells、3,999 snapshots、180 cells 有预测、48 cells 无预测、展示 5,838 条 HGB/recent-slope 预测；静态 contract tests 和 JS syntax 通过。 |
| UI 独立体验/可访问性 QA | FAIL | 没有真实浏览器截图、键盘操作、屏幕阅读器、触控/窄屏和十秒理解测试的签收证据。 |
| 文档一致性 | FAIL | 多处旧状态仍称 EDA/QA 未开始或 UI 仍是 synthetic prototype；旧 NO-GO audit 未标为 superseded。 |
| Seminar Markdown | PARTIAL | 内容和主要数字基本对齐当前 B-only run，且有 speaker notes。 |
| PPTX | FAIL | 现有 PPTX 是 stale legacy export；22 个 slide XML、0 个 PowerPoint notes；无重建/视觉 QA 证据。 |
| 单元测试/lint/核心类型检查 | PASS | `224 passed`；Ruff lint/format、`mypy src`、`uv lock --check`、`git diff --check` 通过。 |
| Canonical broad type gate | FAIL | `uv run mypy src tests scripts` 报 56 个错误，分布于 5 个文件。 |
| Secrets/raw-data boundary | PASS WITH LIMITATIONS | raw data 与 formal artifacts 被 Git ignore；未发现明显 key/PII；但仓库没有权威 secrets/PII scanner 工具。 |
| Git 可提交状态 | FAIL | 5 个 tracked modifications、46 个 untracked entries、0 staged；无 remote/upstream，不能创建 PR 或证明可克隆复现。 |

## 1. 官方数据与科学边界

### 1.1 已验证来源

- KITopen：<https://publikationen.bibliothek.kit.edu/1000169196>
- Dataset DOI：<https://doi.org/10.35097/1969>
- Data descriptor：<https://doi.org/10.1038/s41597-024-03831-x>
- 官方 scripts：<https://github.com/energystatusdata/bat-age-data-scripts>
- 许可：CC BY 4.0

本地官方 capacity result archive：

```text
/tmp/sciencesloop-kit-recovery-20260801/relevant/cell_eocv2.zip
bytes: 93,167,507
sha256: 95de03af7d73efdeed97fcd13fbdca2eeabe85b2971b08394b665c8639b3e140
```

该 hash 与项目文档和正式 source manifest 对齐。完整 result tar 与 EIS archive 也仍在本地
恢复目录，但本轮 B-only run 没有读取 EIS。

### 1.2 独立单位和数据量

- 228 个真实物理电芯；
- 76 个 operating conditions；
- 每个 condition 三个 replicate cells；
- 3,999 个标准容量检查点；
- 2,919 个检查点满足 prefix history 与 exact H3；
- calendar 1,134、cyclic 1,190、driving profile 595 个 eligible snapshots；
- 456 个 `insufficient_history`；
- 624 个 `no_h3_followup`。

这些 2,919 个 snapshots 是同一批电芯的重复时点，不是 2,919 块独立电池。独立性和 split
判断必须以 condition/physical cell 为基础。

### 1.3 Target 与防泄漏

当前 target 为未来第三个 capacity check-up 的 SoH loss：

```text
F1 = SoH_t - SoH_t+3
```

本轮 feature view `B` 仅使用 prediction time 当时可见的容量历史、已发生 exposure 和已知工况。
无 H3 follow-up 不会被当作零损失；未来数据不会进入 feature。fold registry 和独立 review 均
确认 complete-condition-held-out 分组没有交叉。

## 2. 正式 B-only 运行

本地 immutable lineage：

```text
artifacts/local/formal-runs/kit-f1-b-capacity-sequence-v1-20260802T1510-cdt/
artifacts/local/governance/kit-f1-b-capacity-sequence-v1-postrun-independent-review-20260802.{md,json}
```

`engine_summary.json` 核验结果：

```text
run_kind = KIT_F1_B_CAPACITY_SEQUENCE_V1
view = B
planned_fits = 18
completed_fits = 18
prediction_rows = 11676
selection_performed = false
formal_model_result = false
protected_test_result = false
publication_ready = false
resume_approved = false
```

这说明训练确实运行过，但它被有意限制为 development OOF；不能把 `formal_model_result=false`
曲解成“模型没训练”，也不能把 completed training 曲解成“最终模型已定”。

### 2.1 主要 development OOF 结果

Primary metric 为 condition-balanced MAE，单位为 SoH 百分点（pp）：

| 老化模式 | Persistence | Recent slope | ElasticNet | HGB | 最低观察值 |
|---|---:|---:|---:|---:|---|
| Calendar | 1.130 | 0.242 | 0.209 | **0.139** | HGB |
| Cyclic | 9.448 | **5.026** | 5.113 | 5.809 | Recent slope |
| Driving profile | 6.051 | **2.988** | 4.791 | 4.239 | Recent slope |

独立 reviewer 重算结果与 run summary 的最大绝对差为 `1.78e-15`。这支持的结论是：模型相对
表现依赖 ageing mode。它不支持“一个模型在所有模式都是冠军”或“ML 总体优于简单趋势”。

### 2.2 当前可以说什么

允许：

> 在完整实验条件分组的 KIT development OOF 中，HGB 在 calendar ageing 得到最低 MAE；
> recent-slope baseline 在 cyclic 和 driving-profile ageing 得到最低 MAE。结果显示模型优势
> 明显依赖 ageing mode。

不允许：

- HGB 是 KIT 的最终最佳模型；
- 已完成 protected/external test；
- EIS 已证明增加预测价值；
- 已预测电池故障、热失控或 RUL；
- 5 pp 是维修阈值；
- 模型可以决定换电池；
- 当前数字可直接用于文章、生产、简历或 ROI 声明。

## 3. 为什么完整主项目仍未完成

主项目最重要的科学问题是：

> 在相同 snapshots、folds 和 model family 下，`D_CORE = B + EIS` 是否优于 `B`？

当前 B-only milestone 只回答：容量历史和工况下，四种固定方法在三个 ageing modes 的
development OOF 表现如何。它没有回答 EIS 增量问题。

尚缺：

1. EIS 与 capacity snapshot 的 completion/retry/alignment 语义独立审核；
2. 完全相同 eligible rows 上的 paired `B` / `D_CORE` feature matrices；
3. 相同 model family、fold 和 budget 下的 paired OOF；
4. condition-level paired delta 与 uncertainty；
5. 冻结 selection rule 后的模型选择；
6. 单独授权的 protected evaluation；
7. external/prospective evidence。

如果“完成”的定义仍是原始 flagship question，那么项目现在明确是 **未完成**。不能因为 B-only
已经跑通而降低终点定义。

## 4. UI 完成度

### 4.1 静态数据映射通过

`demo/fleet-replay/kit-development-oof.js` 的 metadata：

```text
rosterCount = 228
snapshotCount = 3999
cellsWithPrediction = 180
cellsWithoutPrediction = 48
predictionRowsShown = 5838
featureView = B (capacity history + context; no EIS)
horizon = 3
selectionPerformed = false
protectedTestResult = false
publicationReady = false
```

UI 有 mode tabs、play/pause、前后一步、slider、speed、truth maturation、missing reason 和 review
queue。展示的 5,838 rows 只包含 HGB 与 recent-slope；ElasticNet 和 persistence 仍在完整 run
artifact 中，但不是 UI display subset。这个取舍必须继续在 UI 文档里明确，避免读者把两条曲线
误认为全部模型。

本地运行方式：

```bash
python -m http.server 8000 --directory demo/fleet-replay
```

打开 <http://127.0.0.1:8000/>。已核验首页、JS、CSS、数据 JS 均可通过 HTTP 读取，JS syntax
检查通过。

### 4.2 仍缺独立 UI QA

当前静态 contract tests 不能替代真实浏览器验收。交付前至少需要：

- 桌面与窄屏截图；
- Chrome 实际 play/pause/slider/mode/filter/cell selection 操作；
- 键盘导航和 focus 顺序；
- status 不仅依赖颜色；
- reduced motion/autoplay 行为；
- beginner 十秒理解检查；
- truth 尚未成熟、无 H3、历史不足等 empty states；
- 明确记录 reviewer、日期和结论。

因此 UI 代码/data mapping 可判 PASS，但 release QA 仍 FAIL。

## 5. 文档、报告与文章

多数 live 文档已经准确写出 B-only 训练完成，并明确 D_CORE/selection/protected 未完成：

- `README.md`
- `docs/live-early-warning-tasks.md`
- `docs/early-warning-runbook.md`
- `docs/early-warning-protocol.md`
- `docs/early-warning-model-selection-plan.md`
- `docs/run-fleet-replay-ui.md`

但仍有会误导读者的旧内容：

1. `docs/reports/battery-early-warning-project-report-zh.md` 仍写“当前 UI 仍是 synthetic
   interaction prototype”，与已接入真实 B-only development OOF 的页面冲突。
2. `docs/process/early-warning-development-process.md` 仍有“EDA 没有 reviewed artifact”、
   “parser/audit QA remains open”等阶段性措辞，未清楚区分已通过的 B-only capacity lineage 与
   仍待审的 EIS lineage。
3. `docs/data-card-kit-v2.md` 顶部仍称 parser/audit QA pending；应该准确拆成 capacity-sequence
   已审核、EIS alignment 仍 pending。
4. `docs/audits/kit-f1-o2-target-readiness-zh.md` 仍以“源文件消失”给出旧 NO-GO；源文件已经恢复。
   历史 audit 可以保留，但必须显著标记 `superseded` 并链接到后续独立 semantic review 和正式
   run evidence。
5. `docs/product/kit-oof-replay-ui-spec-v1.md` 部分 acceptance 文案仍以“accepted OOF 尚不存在”
   为条件，应该区分当前 reviewed B-only development mode 与未来 final/protected mode。

结论：内容主体不错，但 canonical documentation consistency gate 未通过。

## 6. Slides / seminar 交付

Markdown seminar source 有 21 个逻辑页面，主要数字和 claim boundary 与 B-only run 对齐，并在
Markdown comments 中包含 speaker notes。

现有 `battery-early-warning-research-seminar.pptx` 不能作为最终 deck：

- 文件是旧导出；source 自己明确写 `stale legacy export`；
- PPTX 内有 22 个 slide XML，与当前 21 页 Markdown 不一致；
- PowerPoint notes 数量为 0；
- 没有 PDF/image render、逐页截图或视觉 QA 记录；
- 没有证明表格、图、字体、页脚和 claim labels 没有溢出或丢失。

交付前需要从当前 Markdown 重新生成 PPTX，保留 speaker notes，并完成逐页视觉检查。当前 slides
source 为 PARTIAL，PPTX 为 FAIL。

## 7. 软件质量验证

已通过：

```text
uv run pytest -q                      -> 224 passed
uv run ruff check .                   -> PASS
uv run ruff format --check .          -> PASS (62 files)
uv run mypy src                       -> PASS (16 source files)
uv lock --check                       -> PASS
git diff --check                      -> PASS
node --check demo/fleet-replay/app.js -> PASS
node --check UI data/fixture JS        -> PASS
```

但 canonical broad type gate 失败：

```text
uv run mypy src tests scripts
-> 56 errors in 5 files (36 files checked)
```

涉及：

- `scripts/build_fleet_replay_data.py`
- `tests/test_kit_target_readiness.py`
- `tests/test_fleet_replay_ui_contract.py`
- `tests/test_kit_f1_b_governance.py`
- `tests/test_kit_dev_training.py`

不能用“`mypy src` 已通过”掩盖仓库自己的广义 Gate G 失败。需要修复 typing 或正式、审慎地重新
定义并记录 canonical gate；不能通过删检查或扩大 ignore 放行。

## 8. 运行治理中的非阻断发现

独立 post-run review 对当前 B-only bounded interpretation 给出批准，但指出两项后续修复：

1. `telemetry.jsonl` 在 line 11 附近有一次相邻 timestamp/elapsed 次序倒转；
2. 没有显式 numeric protected-label-read counter；当前只能根据 B-only 结构证明 protected read 为零。

这两项不推翻本次 B-only development 指标，但下一条 formal/protected/publication lineage 前必须
修复。不得回写或静默修改当前 immutable run；修复应只作用于新的 lineage。

## 9. Git、可复现性和发布状态

当前工作树：

```text
branch: protocol/soh-target-v2
HEAD: 57f98bd
tracked modified: 5
untracked entries: 46
staged: 0
remote/upstream: none
```

这意味着：

- 当前主要实现、文档、UI 和 tests 尚未形成可审查 commit；
- 没有 remote，无法创建 PR；
- 第三方无法从 GitHub clone 当前成果；
- raw data/formal artifacts 被正确 ignore，但需要公开、可复现的 download/checksum/cache runbook
  说明如何重建，而不能只依赖 `/tmp`。

秘密/PII spot-check 没发现明显 key、private key 或个人信息。`data/raw/` 和 `artifacts/` 均被
ignore，官方 archives 没有进入 Git。不过仓库没有权威 scan script，因此这里只能判
`PASS WITH LIMITATIONS`。

## 10. 完成前的精确 blocker 列表

### P0：必须先确定“主项目完成”的终点

- 如果终点只是 B-only development milestone：科学运行已完成，但交付包装仍未完成。
- 如果终点是原始 KIT flagship question：必须继续完成 paired `D_CORE-B` EIS incremental study；
  当前不能宣布项目完成。

### P0：统一 canonical 文档

- 修复第 5 节列出的矛盾；
- 将旧 NO-GO audit 标记为 superseded，不删除历史；
- 所有页面统一写清 B-only 已完成、EIS/protected/selection 未完成；
- CALCE/Cambridge 明确保留为停止/历史 lineage，不再作为当前任务。

### P1：修复 broad mypy

- 清除 56 个类型错误；
- 重新运行 full pytest、Ruff、broad mypy、lock、diff check；
- 不削弱 safety/claim tests。

### P1：完成独立 UI QA

- 做真实浏览器交互、窄屏、键盘和可访问性验收；
- 保留截图/记录；
- 明确 UI 只显示 HGB 和 recent slope。

### P1：重建研究 seminar deck

- 从最新 Markdown 重建 PPTX；
- 保留 speaker notes；
- 逐页 render/视觉 QA；
- 校验 slide count、数字、claim boundary 和来源。

### P1：形成干净 Git 交付

- 审查 51 个 dirty entries 的 scope；
- 进行 secrets/PII/large-file scan；
- 分成可审查 commits；
- 配置 remote 并 push；
- 在 PR 中保留验证证据；
- 不提交 raw KIT archives 或 ignored formal artifacts。

### P2：新 lineage 前的治理修复

- 修复 telemetry 单调性/并发写入行为；
- 增加显式 protected-read counter；
- 不修改当前 immutable B-only artifacts。

## 最终判定

### 可以验收的部分

**B-only real-data development OOF milestone：PASS WITH LIMITATIONS。**

它是真实训练，不是纸上计划；数据、target、split、18/18 fits、OOF predictions 和独立复算均有
证据。当前最有价值的发现是：不同 ageing modes 的描述性最优方法不同，简单 recent-slope 在
cyclic/profile 仍是强基线。

### 不能验收的部分

**完整 KIT 主项目与最终发布包：FAIL。**

EIS 增量主问题没有完成，protected/external evaluation 没有运行，模型没有选择，文档/QA/slides/
Git packaging 仍有明确 blocker。只有完成上述 blockers，才可以再做一次 final completion audit。
