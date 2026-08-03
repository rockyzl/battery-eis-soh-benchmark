# KIT fleet replay 独立 UI / 可访问性 / 声明 QA

> **SUPERSEDED / HISTORICAL — 不代表当前 paired UI 状态。** 本文验收的是旧 B-only 页面，
> 正文中的 B-only / 无 EIS 声明仅保留作历史。当前权威状态是：B/D_CORE development OOF 已
> 完成 54/54 fits、2,919 个相同 eligible snapshots 和 23,352 条 OOF predictions；O2=0，且
> 没有 selection、protected test、publication 或 deployment。结果不能证明统一赢家或 EIS
> 因果效应。KIT 是唯一活动项目；CALCE 已停止，Cambridge 仅为冻结历史材料。当前视觉 QA
> 见 `docs/audits/kit-paired-ui-visual-qa-2026-08-02.md`。

日期：2026-08-02
结论：**APPROVE**
阻断项：**0**

## 1. 验收范围

本次独立验收对象为 `demo/fleet-replay/` 的 KIT 228-cell 历史回放页面。验收覆盖：

- 桌面宽度与窄屏真实浏览器呈现；
- 键盘操作、模式筛选、播放控制、时间滑块、研究复核队列和电芯详情；
- 228 / 180 / 48 以及各 ageing mode 的数量；
- Development OOF、B-only、无 EIS、无 km、未选模型、非生产报警等声明边界；
- 页面指标与 governed formal run 的一致性；
- 静态投影可复现性、工件哈希和 formal run 不可变性；
- 11 项 UI 契约测试及仓库完整相关检查。

未执行部署、Git 发布或 CALCE 工作，也未修改正式运行目录。

## 2. 最终判定

页面通过本轮独立 QA，可以作为 **KIT condition-held-out Development OOF 的本地研究回放界面** 使用。

这个批准不改变证据等级。页面仍然不是最终模型、protected test、生产监控、故障诊断、换电池建议或模型选择结果。

## 3. 浏览器与交互证据

使用可见 Windows Chrome `150.0.7871.187` 连接本地静态服务验收。

| 检查 | 结果 |
|---|---|
| 桌面 `1440 × 1000` | 通过；无横向溢出 |
| 窄屏 `390 × 844` | 通过；单列重排，无横向溢出 |
| 默认状态 | 检查 10；播放未自动开始；`aria-pressed=false` |
| 上一步 / 下一步 | 10 → 11 → 10，详情、队列和成熟结果同步更新 |
| 播放 / 暂停 | 播放后推进到 11，按钮变为暂停且 `aria-pressed=true`；再次操作后停止 |
| 滑块 | 可跳到检查 13，相关容量、SoH、预测和成熟结果同步更新 |
| 模式键盘导航 | `ArrowRight`、`Home`、`End` 均通过，焦点与 `aria-selected` 同步 |
| 研究复核队列 | 选择队首后，详情切换到同一 cell，并显示模型分歧原因 |
| Fleet tile | 点击后选中态、cell identity、当前值、预测和图表同步更新 |
| 技术详情 | `summary` 获得键盘焦点后按 Enter 可展开，焦点轮廓清楚 |
| 浏览器错误 | console error `0`；page error `0` |

模式筛选实测：

| 模式 | 显示数量 |
|---|---:|
| 全部 | 228 / 228 |
| Calendar | 48 / 228 |
| Cyclic | 144 / 228 |
| Driving profile | 36 / 228 |

截图：

- [桌面完整页面](screenshots/kit-ui-qa-desktop.png)
- [窄屏完整页面](screenshots/kit-ui-qa-narrow.png)
- [键盘展开技术详情与焦点状态](screenshots/kit-ui-qa-detail-keyboard.png)

## 4. 数据数量与状态

生成数据与正式运行工件一致：

- 228 个唯一电芯；
- 76 个实验条件，每个条件 3 个 replicate；
- 3,999 个 capacity snapshots；
- 180 个电芯至少有一个 B-view OOF prediction；
- 48 个电芯全程没有满足本次 H3/B-view 预测条件；
- 页面显示 5,838 个预测值，即 2,919 个 eligible snapshots × HGB / recent slope 两个方法；
- formal run 的 `oof_predictions.jsonl` 有 11,676 行，包含四个模型；页面生成器只投影经过允许的 HGB 与 recent-slope 两个模型。

页面在全部模式下始终保留 228-cell roster。没有当前记录、历史不足和没有 H3 follow-up 均以文字说明，没有用零或虚构值补空。

## 5. 指标与声明边界

页面显示值与 `engine_summary.json` 中的 condition-balanced Development OOF 指标一致：

| Mode | HGB MAE | Recent slope MAE | 页面结论 |
|---|---:|---:|---|
| Calendar | 0.139397 pp | 0.241981 pp | HGB 较低 |
| Cyclic | 5.808529 pp | 5.025917 pp | recent slope 较低 |
| Driving profile | 4.238517 pp | 2.987732 pp | recent slope 较低 |

声明检查通过：

- 6 个 `data-development-label` 均存在且在桌面、交互后和窄屏状态可见；顶部 sticky banner 持续说明 `DEVELOPMENT OOF`；
- Final / protected test 选项禁用，页面不能切入不存在的 final 模式；
- `selection_performed=false`、未使用 protected test、不是最终模型、不是生产报警等文字明确可见；
- 研究复核队列明确是模型分歧排序，不是故障概率或换电池建议；
- 页面明确说明 B-only artifact 不含 EIS 或公里数；Driving profile 详情另显示 `km 不在本 artifact 中`；
- 技术详情显示 feature view B、outer fold、cell、目标定义和来源 artifact SHA-256；
- 未发现 RUL、安全、故障识别、生产收益、维护动作或统一冠军等越界声明。

## 6. 可访问性检查

- 页面语言为 `zh-CN`，结构包含 header、main、section、aside；
- mode tabs 使用 `tablist` / `tab` / `aria-selected` / roving tabindex；
- 播放按钮公开 pressed 状态，不自动播放；
- slider 有 label、output、最小值、最大值和 step；
- 状态同时使用文字和颜色，fleet tile 有完整的 `aria-label`；
- 动态证据 banner 与成熟结果使用 `aria-live`；
- 图表有 title / desc，预测线与真实测量线使用不同样式和文字图例；
- focus-visible 轮廓在键盘操作时清楚可见；
- `prefers-reduced-motion` 下关闭动画和滚动过渡；
- 页面未使用 `eval`、`new Function` 或 `document.write`。

## 7. 工件绑定与不可变性

页面 metadata 与正式运行工件哈希一致：

- `oof_predictions.jsonl`：`479d67f377106e04c2adb498ceb3643c1c854e12cd62c39413c0d6bc3b377c13`
- `engine_summary.json`：`0e4f1382b3b92d9b8d4fda2b2d0b120fc4a2762bdc1260fee1b02fec3dd1e551`
- `snapshot_manifest.jsonl`：`4623aed2fca435d9679d12927baf15a64ac8c94c3934d8366b1269730369c472`
- `fold_registry.json`：`4c0d3bb3fc0bd3b46a57686a2b3d95c1f5af127fdab32b325591a18b0e8f8aaf`
- `source_manifest.json`：`84b48a3e8d52e874d7f7b9b905fd0bb1facb851c3dce56cf7ec879e941961b54`

重新把同一 formal run 投影到 `/tmp/kit-development-oof.qa.js`，与页面现有数据逐字节相同；两者 SHA-256 均为：

`b5f7cb22128426b23c558068562634a9f8dec55749897d42739604afff369f80`

生成前后 formal run 内 10 个文件的 SHA-256 均未变化。生成器只读 formal run，并把临时输出写到 `/tmp`。

## 8. 自动检查

```text
uv run pytest tests/test_fleet_replay_ui_contract.py -q
11 passed in 0.06s

uv run pytest -q
224 passed in 89.22s

uv run ruff check .
All checks passed!

uv run ruff format --check .
64 files already formatted

uv run mypy src tests scripts
Success: no issues found in 36 source files
```

UI runbook 中列出的目标文件 `git diff --check` 也通过。

## 9. 本轮唯一修改

首轮浏览器检查发现：首屏已经显示 B-only 和无公里数，但“未使用 EIS”只在折叠技术详情内。为使限制无需展开即可看见，本轮将可见 microcopy 从：

`本次 B-only artifact 不含公里数`

改为：

`本次 B-only artifact 不含 EIS 或公里数`

同时在既有第 5 项 UI 契约测试中加入对应断言；测试总数仍为 11。除此之外没有修改 UI 行为、数据、模型、正式工件或声明等级。

## 10. 保留限制

以下是设计边界，不是本轮缺陷：

- 只有 Development OOF，没有 protected external test；
- `selection_performed=false`，不能把页面解读为已经选出最终模型；
- 只展示 B view，不包含 EIS；
- 数据 artifact 不提供公里数；
- 研究复核队列只是分歧/可用性 triage，不是报警系统。

在这些边界保持不变的前提下，本轮结论为 **APPROVE**。
