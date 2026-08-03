"""Static contracts for the KIT 228-cell paired development-OOF replay."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "fleet-replay"
HTML_PATH = DEMO / "index.html"
CSS_PATH = DEMO / "styles.css"
APP_PATH = DEMO / "app.js"
DATA_PATH = DEMO / "kit-development-oof.js"
SUMMARY_PATH = DEMO / "data" / "kit-paired-development-summary.json"
METRICS_PATH = DEMO / "data" / "kit-paired-development-metrics.csv"
DELTAS_PATH = DEMO / "data" / "kit-paired-development-deltas.csv"
GENERATOR_PATH = ROOT / "scripts" / "build_fleet_replay_data.py"
SNAPSHOT_MANIFEST_SHA256 = "810d58c29ee8c3458cda80cef0cb1e0d08a3e8bee2d17e5a95b405db35a0608f"
CURRENT_DOCS = (
    ROOT / "docs" / "early-warning-protocol.md",
    ROOT / "docs" / "early-warning-runbook.md",
    ROOT / "docs" / "early-warning-model-selection-plan.md",
)
SUPERSEDED_AUDITS = (
    ROOT / "docs" / "audits" / "kit-f1-o2-target-readiness-zh.md",
    ROOT / "docs" / "audits" / "kit-final-project-completion-audit-zh.md",
    ROOT / "docs" / "audits" / "kit-processed-result-independent-semantic-review-zh.md",
    ROOT / "docs" / "audits" / "kit-ui-independent-qa-zh.md",
)


def _load_generator_module() -> Any:
    spec = importlib.util.spec_from_file_location("fleet_replay_builder", GENERATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fleet replay builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _HTMLContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


def _load_data(source: str) -> dict[str, Any]:
    prefix = "export const kitDevelopmentOof = Object.freeze("
    body = source.split("\n", 1)[1]
    assert body.startswith(prefix) and body.endswith(");\n")
    value = json.loads(body[len(prefix) : -3])
    assert isinstance(value, dict)
    return cast("dict[str, Any]", value)


@pytest.fixture(scope="module")
def assets() -> dict[str, str]:
    paths = (
        HTML_PATH,
        CSS_PATH,
        APP_PATH,
        DATA_PATH,
        SUMMARY_PATH,
        METRICS_PATH,
        DELTAS_PATH,
        GENERATOR_PATH,
    )
    missing = [path for path in paths if not path.is_file()]
    assert not missing, f"Fleet replay assets are missing: {missing}"
    return {path.name: path.read_text(encoding="utf-8") for path in paths}


@pytest.fixture(scope="module")
def parsed(assets: dict[str, str]) -> _HTMLContractParser:
    parser = _HTMLContractParser()
    parser.feed(assets["index.html"])
    return parser


@pytest.fixture(scope="module")
def data(assets: dict[str, str]) -> dict[str, Any]:
    return _load_data(assets["kit-development-oof.js"])


def test_demo_is_dependency_free_local_static(
    assets: dict[str, str], parsed: _HTMLContractParser
) -> None:
    html_tags = [attrs for tag, attrs in parsed.tags if tag == "html"]
    assert html_tags and html_tags[0].get("lang") == "zh-CN"
    assert {"header", "main"}.issubset({tag for tag, _ in parsed.tags})
    assert re.search(r'href=["\'](?:\./)?styles\.css["\']', assets["index.html"])
    assert re.search(r'src=["\'](?:\./)?app\.js["\']', assets["index.html"])
    assert 'from "./kit-development-oof.js"' in assets["app.js"]
    assert not re.search(r"(?:src|href)=[\"']https?://", assets["index.html"], re.IGNORECASE)


def test_real_roster_and_paired_support_are_exact(data: dict[str, Any]) -> None:
    metadata = data["metadata"]
    cells = data["cells"]
    assert metadata["rosterCount"] == 228
    assert metadata["conditionCount"] == 76
    assert metadata["snapshotCount"] == 3999
    assert metadata["eligibleSnapshotCount"] == 2919
    assert metadata["cellsWithPrediction"] == 180
    assert metadata["cellsWithoutPrediction"] == 48
    assert metadata["predictionRowsShown"] == 23352
    assert metadata["completedFits"] == 54
    assert len(cells) == 228
    assert len({cell["id"] for cell in cells}) == 228
    assert sum(len(cell["snapshots"]) for cell in cells) == 3999
    assert len({cell["condition"] for cell in cells}) == 76
    assert {cell["replicate"] for cell in cells} == {1, 2, 3}


def test_development_boundary_is_persistent_and_final_mode_fails_closed(
    parsed: _HTMLContractParser, data: dict[str, Any]
) -> None:
    text = parsed.text
    assert "DEVELOPMENT OOF — 不是最终模型，也不是生产报警" in text
    assert "未使用 protected test" in text
    assert "publication_ready=false" in text
    assert "selection_performed=false" in text
    assert "不是物理仿真、实时监控、protected external test、故障诊断或换电池建议" in text
    accepted = [
        attrs
        for tag, attrs in parsed.tags
        if tag == "option" and attrs.get("value") == "accepted-kit-oof"
    ]
    assert accepted and all("disabled" in attrs for attrs in accepted)
    metadata = data["metadata"]
    for flag in (
        "selectionPerformed",
        "formalModelResult",
        "scientificResult",
        "protectedTestResult",
        "publicationReady",
        "resumeApproved",
        "deploymentReady",
    ):
        assert metadata[flag] is False


def test_first_screen_and_eis_increment_explanation_are_plain(parsed: _HTMLContractParser) -> None:
    for label in ("现在做什么？", "电芯总数", "历史进度", "现在先看谁？"):
        assert label in parsed.text
    assert "预测未来 3 次检查的 SoH 损失" in parsed.text
    assert "180 个有 paired OOF" in parsed.text
    assert "加上 EIS，预测真的更好吗" in parsed.text
    assert "没有统一答案" in parsed.text
    assert "D_CORE − B" in parsed.text
    assert "不是因果检验" in parsed.text


def test_all_ageing_modes_playback_and_absence_states_are_explicit(
    assets: dict[str, str], parsed: _HTMLContractParser
) -> None:
    for label in ("Calendar · 静置老化", "Cyclic · 反复充放电", "Driving profile · 驾驶工况"):
        assert label in parsed.text
    ids = {attrs.get("id") for tag, attrs in parsed.tags if tag in {"button", "input", "select"}}
    assert {"replay-play", "replay-prev", "replay-next", "replay-slider", "model-family"}.issubset(
        ids
    )
    combined = assets["index.html"] + assets["app.js"]
    for state in ("历史不足", "没有未来第 3 次检查", "这个检查点没有记录"):
        assert state in combined
    assert "D_CORE 使用当前检查点匹配的 EIS 特征" in parsed.text
    assert "km 不在本 artifact 中" in assets["app.js"]


def test_metrics_deltas_and_retry_rows_have_collision_free_composite_identity(
    data: dict[str, Any], assets: dict[str, str]
) -> None:
    metrics = data["metrics"]
    deltas = data["pairedDeltas"]
    retry = data["retrySensitivity"]
    metric_keys = {
        (row["mode"], row["policy"], row["view"], row["family"], row["pairedDesignSha256"])
        for row in metrics
    }
    delta_keys = {(row["mode"], row["policy"], row["family"]) for row in deltas}
    assert len(metrics) == len(metric_keys) == 24
    assert len(deltas) == len(delta_keys) == 12
    assert len(retry) == 6
    assert assets["kit-paired-development-metrics.csv"].count("\n") == 25
    assert assets["kit-paired-development-deltas.csv"].count("\n") == 13


def _find(rows: list[dict[str, Any]], **identity: str) -> dict[str, Any]:
    matches = [row for row in rows if all(row[key] == value for key, value in identity.items())]
    assert len(matches) == 1
    return matches[0]


def test_key_paired_results_match_governed_summary(data: dict[str, Any]) -> None:
    metrics = data["metrics"]
    deltas = data["pairedDeltas"]
    cyclic_hgb_b = _find(metrics, mode="cyclic", policy="primary", view="B", familyKey="hgb")
    cyclic_hgb_d = _find(metrics, mode="cyclic", policy="primary", view="D_CORE", familyKey="hgb")
    assert cyclic_hgb_b["mae"] == pytest.approx(5.808529, abs=1e-6)
    assert cyclic_hgb_d["mae"] == pytest.approx(5.183636, abs=1e-6)
    cyclic_hgb_delta = _find(deltas, mode="cyclic", policy="primary", familyKey="hgb")
    assert cyclic_hgb_delta["maeDelta"] == pytest.approx(-0.624893, abs=1e-6)
    assert cyclic_hgb_delta["maeCi95"] == pytest.approx([-1.056359, -0.240783], abs=1e-6)
    assert cyclic_hgb_delta["interpretation"] == "improvement_signal"
    cyclic_elastic = _find(deltas, mode="cyclic", policy="primary", familyKey="elasticNet")
    assert cyclic_elastic["maeDelta"] == pytest.approx(0.804445, abs=1e-6)
    assert cyclic_elastic["interpretation"] == "worsening_signal"
    profile_hgb = _find(deltas, mode="profile", policy="primary", familyKey="hgb")
    assert profile_hgb["interpretation"] == "mixed_rmse_unclear_mae_worse"


def test_every_eligible_prediction_uses_nested_policy_view_family_keys(
    data: dict[str, Any],
) -> None:
    eligible = 0
    prediction_count = 0
    predicted_cells: set[str] = set()
    for cell in data["cells"]:
        for snapshot in cell["snapshots"]:
            predictions = snapshot["pred"]
            prediction_count += sum(
                len(families) for views in predictions.values() for families in views.values()
            )
            if snapshot["status"] != "eligible_f1_and_d_core":
                assert predictions == {}
                continue
            eligible += 1
            predicted_cells.add(cell["id"])
            assert snapshot["h3i"] == snapshot["i"] + 3
            assert snapshot["h3s"] is not None and snapshot["h3loss"] is not None
            assert set(predictions) == {"primary", "keep_last_sensitivity"}
            assert set(predictions["primary"]) == {"B", "D_CORE"}
            assert set(predictions["primary"]["B"]) == {
                "persistence",
                "recentSlope",
                "elasticNet",
                "hgb",
            }
            assert set(predictions["primary"]["D_CORE"]) == {"elasticNet", "hgb"}
            assert set(predictions["keep_last_sensitivity"]) == {"D_CORE"}
            assert set(predictions["keep_last_sensitivity"]["D_CORE"]) == {"elasticNet", "hgb"}
    assert eligible == 2919
    assert len(predicted_cells) == 180
    assert prediction_count == 23352


def test_truth_maturation_and_review_queue_are_not_alarm(
    assets: dict[str, str], parsed: _HTMLContractParser
) -> None:
    app = assets["app.js"]
    assert "state.checkup - HORIZON" in app
    assert "还不能核对" in app
    assert "检查 ${originIndex} 的预测，现在有答案了" in app
    assert "页面不会用零或虚构数字填空" in app
    assert "Math.abs(b.loss - dCore.loss)" in app
    assert "加入 EIS 前后相差" in app
    assert "这不是故障概率，也不是换电池建议" in parsed.text
    assert "not an alarm" in parsed.text


def test_accessibility_and_no_dangerous_dynamic_code(
    assets: dict[str, str], parsed: _HTMLContractParser
) -> None:
    assert any(attrs.get("role") == "tablist" for _, attrs in parsed.tags)
    assert any(tag == "details" for tag, _ in parsed.tags)
    assert any(tag == "summary" for tag, _ in parsed.tags)
    assert any("aria-live" in attrs for _, attrs in parsed.tags)
    assert "ArrowLeft" in assets["app.js"] and "ArrowRight" in assets["app.js"]
    scripts = assets["app.js"] + assets["kit-development-oof.js"]
    for pattern in (r"\beval\s*\(", r"\bnew\s+Function\s*\(", r"document\.write\s*\("):
        assert not re.search(pattern, scripts)


def test_generated_projection_is_bound_to_governed_artifacts(
    data: dict[str, Any], assets: dict[str, str]
) -> None:
    metadata = data["metadata"]
    assert metadata["schemaVersion"] == "kit_paired_development_projection_v1"
    assert metadata["artifactSchema"] == "kit_f1_o2_dev_v2"
    assert metadata["sourceRunId"] == "kit-f1-paired-b-dcore-dev-v2-20260802T201053-cdt"
    generator_module = _load_generator_module()
    start_manifest = json.loads(
        (generator_module.DEFAULT_RUN / "start_manifest.json").read_text(encoding="utf-8")
    )
    assert start_manifest["run_kind"] == "KIT_F1_PAIRED_B_D_CORE_DEVELOPMENT_V2"
    assert metadata["runKind"] == start_manifest["run_kind"]
    assert metadata["artifactValidationPassed"] is True
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in metadata["sourceHashes"].values())
    assert metadata["sourceHashes"]["snapshotManifest"] == SNAPSHOT_MANIFEST_SHA256
    generator = assets["build_fleet_replay_data.py"]
    for guard in (
        "expected exactly 23,352 OOF prediction rows",
        "duplicate composite OOF prediction identity",
        "composite prediction key collision",
        "expected exactly 12 paired-delta identities",
        "O2 output must remain empty",
        "This script never edits the governed run directory",
    ):
        assert guard in generator


def test_mutated_snapshot_manifest_fails_governed_hash_validation(tmp_path: Path) -> None:
    run_copy = tmp_path / "governed-run"
    run_copy.mkdir()

    negative_claims = {
        "formal_model_result": False,
        "protected_test_result": False,
        "publication_ready": False,
        "scientific_result": False,
        "selection_performed": False,
        "resume_approved": False,
    }
    payloads = {
        "start_manifest.json": {
            "run_kind": "KIT_F1_PAIRED_B_D_CORE_DEVELOPMENT_V2",
        },
        "engine_summary.json": {
            **negative_claims,
            "run_kind": "KIT_F1_O2_DEVELOPMENT_V2",
            "completed_fits": 54,
            "metrics": [{} for _ in range(24)],
            "o2_results": [],
        },
        "paired_deltas.json": {
            **negative_claims,
            "paired_deltas": [{} for _ in range(12)],
        },
        "retry_sensitivity.json": {
            **negative_claims,
            "results": [{} for _ in range(6)],
        },
        "fold_registry.json": {},
    }
    for name, payload in payloads.items():
        (run_copy / name).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    (run_copy / "oof_predictions.jsonl").write_text("", encoding="utf-8")
    (run_copy / "snapshot_manifest.jsonl").write_text(
        json.dumps({"capacity_ah": 2.5}, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts_sha256 = {
        name: hashlib.sha256((run_copy / name).read_bytes()).hexdigest()
        for name in (
            "engine_summary.json",
            "fold_registry.json",
            "oof_predictions.jsonl",
            "paired_deltas.json",
            "retry_sensitivity.json",
            "snapshot_manifest.jsonl",
        )
    }
    (run_copy / "final_manifest.json").write_text(
        json.dumps(
            {
                **negative_claims,
                "artifact_validation_passed": True,
                "exit_code": 0,
                "artifacts_sha256": artifacts_sha256,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_copy / "terminal.json").write_text(
        json.dumps({"status": "completed", "exit_code": 0}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    snapshot_path = run_copy / "snapshot_manifest.jsonl"
    lines = snapshot_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["capacity_ah"] = float(first["capacity_ah"]) + 0.000001
    lines[0] = json.dumps(first, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    snapshot_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"^governed artifact hash mismatch: snapshot_manifest\.jsonl$",
    ):
        _load_generator_module()._validate_governed_run(run_copy)

    (run_copy / "start_manifest.json").write_text(
        json.dumps({"run_kind": "KIT_F1_O2_DEVELOPMENT_V2"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"^unexpected paired start-manifest run kind$"):
        _load_generator_module()._validate_governed_run(run_copy)


def test_compact_summary_matches_browser_projection(
    data: dict[str, Any], assets: dict[str, str]
) -> None:
    summary = json.loads(assets["kit-paired-development-summary.json"])
    assert summary["metadata"] == data["metadata"]
    assert summary["metrics"] == data["metrics"]
    assert summary["pairedDeltas"] == data["pairedDeltas"]
    assert summary["retrySensitivity"] == data["retrySensitivity"]
    assert "EIS universally improves prediction" in summary["forbiddenClaims"]
    metrics_csv = list(csv.DictReader(assets["kit-paired-development-metrics.csv"].splitlines()))
    deltas_csv = list(csv.DictReader(assets["kit-paired-development-deltas.csv"].splitlines()))
    assert len(metrics_csv) == 24 and len(deltas_csv) == 12


def test_current_docs_and_superseded_audits_have_unambiguous_status() -> None:
    for path in CURRENT_DOCS:
        text = path.read_text(encoding="utf-8")
        top = text[:1800]
        for current_fact in (
            "54/54",
            "2,919",
            "23,352",
            "no O2",
            "no O2, model selection, protected test, publication or",
            "CALCE is stopped",
            "Cambridge is frozen historical",
        ):
            assert current_fact in top, f"{path.name} is missing current fact: {current_fact}"
        assert "Status: `B-only" not in top
        assert "paired `D_CORE-B` stage remains pending" not in text

    for path in SUPERSEDED_AUDITS:
        banner = path.read_text(encoding="utf-8")[:1200]
        assert "SUPERSEDED / HISTORICAL" in banner
        assert "54/54" in banner and "2,919" in banner and "23,352" in banner
        assert "O2=0" in banner
