# 运行 KIT paired development-OOF 电芯回放 UI

这个静态页面同时展示：

1. 228 个真实 KIT 电芯沿 completed check-up 的历史回放；
2. 180 个 paired-eligible cells 的 B 与 D_CORE OOF 预测；
3. calendar / cyclic / driving-profile × ElasticNet / HGB 的六组 EIS 增量比较；
4. MAE、RMSE、R²、D_CORE − B 差值和 95% whole-condition bootstrap 区间；
5. 研究复核队列和预测成熟后的历史核对。

它不是最终模型、protected test、实时监控、故障诊断或换电池建议。页面始终显示
`publication_ready=false`、`selection_performed=false`，并明确没有 universal winner 或因果结论。

## 启动

```bash
cd /home/lu2/dev/personal/projects/scifm/battery-eis-soh-benchmark
python -m http.server 8765 --directory demo/fleet-replay
```

打开：<http://127.0.0.1:8765/>

不要直接双击 `index.html`；浏览器通常会阻止 `file://` 页面加载 ES module。

## 重新生成 commit-safe projection

权威 local run 必须存在，然后执行：

```bash
uv run python scripts/build_fleet_replay_data.py
```

脚本只读 governed run，并生成：

- `demo/fleet-replay/kit-development-oof.js`：228-cell browser projection；
- `demo/fleet-replay/data/kit-paired-development-summary.json`：compact metrics/deltas/flags/hashes；
- `demo/fleet-replay/data/kit-paired-development-metrics.csv`：24 metric rows；
- `demo/fleet-replay/data/kit-paired-development-deltas.csv`：12 paired-delta rows。

它不会复制 raw KIT data、authorization grant/receipt 或本地治理文件。若 run kind、hash、claim
flags、O2 状态、计数或 composite identity 漂移，脚本 fail closed。

## 页面怎么读

- 误差差值统一定义为 `D_CORE − B`：负数表示加入 EIS 后误差下降，正数表示误差上升；
- 95% 区间跨 0：当前开发证据无法清楚判断方向；
- R² 是 secondary metric，不能替代 primary condition-balanced MAE；
- 模型家族下拉框只改变 selected-cell replay，不进行模型选择；
- 三次检查后打开的 truth 是历史回放中的成熟标签，不是实时预测结果；
- `检查 10` 是序号，不代表固定天数、EFC 或公里数。

## 验证

```bash
node --check demo/fleet-replay/app.js
uv run pytest tests/test_fleet_replay_ui_contract.py -q
uv run ruff check scripts/build_fleet_replay_data.py tests/test_fleet_replay_ui_contract.py
uv run ruff format --check scripts/build_fleet_replay_data.py tests/test_fleet_replay_ui_contract.py
```
