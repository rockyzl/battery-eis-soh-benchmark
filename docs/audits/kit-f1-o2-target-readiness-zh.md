# KIT F1 / O2 数据与目标就绪审计（中文摘要）

> **SUPERSEDED / HISTORICAL — 不代表当前状态。** 本文保存的是配对运行前的 readiness
> 快照。当前权威状态是：B/D_CORE development OOF 已完成 54/54 fits、2,919 个相同 eligible
> snapshots 和 23,352 条 OOF predictions；O2=0，且没有 selection、protected test、publication
> 或 deployment。结果不能证明统一赢家或 EIS 因果效应。KIT 是唯一活动项目；CALCE 已停止，
> Cambridge 仅为冻结历史材料。请以 `docs/live-early-warning-tasks.md` 为准。

日期：2026-08-01
状态：`NO-GO-A — 当前环境缺少可重新核验的官方源文件`

## 大白话结论

这一步只检查“数据和标签能不能放心用于建模”，不训练任何模型，也不选择对项目有利的
阈值。

F1 的真实答案来自未来第三次标准容量检查：

```text
F1 = 当前 SoH - 第三次未来检查的 SoH
```

O2 只是从同一个连续答案切出一个候选事件：下降至少 5 个百分点。审计同时固定查看
4、5、6 个百分点，只用于判断 5 是否处于一个极不稳定的位置，不能从三个阈值里挑一个
模型分数最好的。

当前不能放行，原因不是模型不好，而是原始 KIT 压缩包原来放在 `/tmp`，现在已经消失。
本轮对 `/tmp`、项目目录、用户目录、Windows Downloads 和
`/mnt/c/Tools/Download Folder` 做了有范围的只读检查，没有找到
`cell_eocv2.zip`、`cell_eisv2.zip` 或官方 333.4 MB 数据包。

## 官方来源核验

- KITopen：<https://publikationen.bibliothek.kit.edu/1000169196>
- DOI：<https://doi.org/10.35097/1969>
- RADAR4KIT：<https://radar.kit.edu/radar/en/dataset/CoNQplSNoVeXExyV>
- 许可：CC BY 4.0
- DataCite 当前元数据：一个 `333.4 MB`、`application/x-tar` 数据包
- KIT 官方说明：228 个 NMC/C+SiO 电芯，包含容量、阻抗和脉冲电阻结果

DataCite 元数据可访问，但本环境访问 DOI/RADAR 下载页和 API 时多次超时，无法获得一个
可验证的直接下载 URL、当前包校验和或文件内容。因此没有使用第三方镜像，也没有把旧
审计数字冒充本轮重新计算结果。

## 审计程序准备检查什么

`scripts/audit_kit_target_readiness.py` 在源文件恢复后会生成本地、Git 忽略的 JSON：

1. 每个电芯的 Q0 是否存在，来源文件、行号、时间戳和 SHA 是否完整；
2. 每种老化模式有多少 snapshot 有第三次未来检查，有多少只是位于记录末尾；
3. F1 是否保留负数（表观恢复或测量波动），绝不裁剪为零；
4. O2 在 4/5/6 pp 下的 snapshot、电芯和 condition 支持；
5. 5±0.5 pp 灰区数量；灰区只描述阈值敏感性，不冒充测量噪声；
6. 三次未来检查跨越的真实天数和 EFC；公里数在当前解析 schema 中没有来源，所以写
   `not available`，绝不推算；
7. 相邻检查变化和同 condition 三个电芯的离散程度，但明确说明三个电芯是物理重复，
   不是同一个电芯的技术重复测量；
8. calendar / cyclic / profile 分开报告，避免把不同物理时钟混在一起。

## GO / NO-GO 标准

### F1 development OOF

只有下面全部满足才可能 `GO`：

- 228 个电芯的 Q0 一一对应且 lineage 完整；
- F1 严格使用第三次未来检查、保留符号；
- 没有 H3 的 snapshot 被标记为 unavailable，不变成 0 或负样本；
- capacity 与 EIS 的完成时间语义有官方或人工审核证据；
- calendar/cyclic/profile 的 condition 与 snapshot 支持清楚。

当前结论：`NO-GO`，因为源文件不可用，而且 timestamp 与 duration 的完成语义仍未被正式
接受。

### O2 candidate reporting

除了 F1 全部条件，还需要证明容量检查的技术重复噪声足以支持一个硬 5 pp 边界。三个同
condition 电芯不能替代技术重复测量。

当前结论：`NO-GO`。即使恢复数据并完成 F1 审计，O2 仍需独立的噪声和决策价值复核。

## 下一步

从官方 RADAR4KIT 下载完整数据包到 `/tmp` 或 Git 忽略的 cache，记录 URL、字节数和
checksum，解出官方 `cell_eocv2.zip` 与 `cell_eisv2.zip`，再运行：

```bash
uv run python scripts/audit_kit_target_readiness.py \
  --eoc-zip <OFFICIAL_CELL_EOCV2_ZIP> \
  --eis-zip <OFFICIAL_CELL_EISV2_ZIP> \
  --output artifacts/finish-wave/A-target-data-readiness.json
```

在审计签字前，不运行 F1/O2 OOF、不选择模型、不解锁 accepted replay，也不写性能结论。
