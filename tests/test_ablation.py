"""Tests for coder_agent/eval/ablation.py — no real LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from coder_agent.eval.ablation import (
    FEATURE_ADDED,
    MARGINAL_PAIRS,
    PRESET_SEQUENCE,
    AblationRunner,
    FeatureDelta,
    compute_feature_deltas,
    print_delta_table,
    write_ablation_report,
)
from coder_agent.eval.metrics import MetricsSummary
from coder_agent.eval.runner import ComparisonReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_summary(
    config_label: str,
    benchmark_pass_rate: float = 0.8,
    strict_success_rate: float = 0.75,
    efficiency_score: float = 0.1,
    retry_cost: float = 0.05,
    avg_steps: float = 8.0,
    n_tasks: int = 10,
) -> MetricsSummary:
    return MetricsSummary(
        config_label=config_label,
        n_tasks=n_tasks,
        task_success_rate=benchmark_pass_rate,
        benchmark_pass_rate=benchmark_pass_rate,
        clean_completion_rate=benchmark_pass_rate,
        strict_success_rate=strict_success_rate,
        partial_credit_score=benchmark_pass_rate,
        efficiency_score=efficiency_score,
        retry_cost=retry_cost,
        avg_steps=avg_steps,
        avg_tokens=1000.0,
        avg_duration=5.0,
    )


def _make_report(config_rates: dict[str, float]) -> ComparisonReport:
    """Build a ComparisonReport with summaries keyed by config label."""
    summaries = {label: _make_summary(label, benchmark_pass_rate=rate)
                 for label, rate in config_rates.items()}
    return ComparisonReport(
        configs=list(config_rates.keys()),
        summaries=summaries,
        raw_results={label: [] for label in config_rates},
    )


# ---------------------------------------------------------------------------
# Group 1: PRESET_SEQUENCE and constants
# ---------------------------------------------------------------------------

def test_preset_sequence_contains_c1_through_c6():
    assert PRESET_SEQUENCE == ("C1", "C2", "C3", "C4", "C5", "C6")


def test_feature_added_keys_match_preset_sequence():
    for preset in PRESET_SEQUENCE:
        assert preset in FEATURE_ADDED, f"FEATURE_ADDED missing entry for {preset}"


def test_marginal_pairs_coverage():
    """Every preset except C1 should appear as the 'config' side exactly once."""
    configs_in_pairs = [pair[0] for pair in MARGINAL_PAIRS]
    expected = [p for p in PRESET_SEQUENCE if p != "C1"]
    assert sorted(configs_in_pairs) == sorted(expected)


# ---------------------------------------------------------------------------
# Group 2: compute_feature_deltas — marginal mode
# ---------------------------------------------------------------------------

def test_compute_feature_deltas_marginal_count():
    report = _make_report({"C1": 0.5, "C2": 0.6, "C3": 0.7, "C4": 0.75, "C5": 0.72, "C6": 0.8})
    deltas = compute_feature_deltas(report, mode="marginal")
    assert len(deltas) == 5


def test_compute_feature_deltas_marginal_c2_vs_c1():
    report = _make_report({"C1": 0.5, "C2": 0.7, "C3": 0.7, "C4": 0.75, "C5": 0.72, "C6": 0.8})
    deltas = compute_feature_deltas(report, mode="marginal")
    c2_delta = next(d for d in deltas if d.config == "C2")
    assert c2_delta.reference == "C1"
    assert c2_delta.benchmark_pass_rate_delta == pytest.approx(0.2)
    assert c2_delta.feature_added == "+planning=react"


def test_compute_feature_deltas_marginal_c5_vs_c3():
    """C5 branches from C3, not C4."""
    report = _make_report({"C1": 0.5, "C2": 0.6, "C3": 0.7, "C4": 0.75, "C5": 0.65, "C6": 0.8})
    deltas = compute_feature_deltas(report, mode="marginal")
    c5_delta = next(d for d in deltas if d.config == "C5")
    assert c5_delta.reference == "C3"
    assert c5_delta.benchmark_pass_rate_delta == pytest.approx(-0.05)


def test_compute_feature_deltas_marginal_c6_vs_c3():
    """C6 branches from C3, not C4 or C5."""
    report = _make_report({"C1": 0.5, "C2": 0.6, "C3": 0.7, "C4": 0.75, "C5": 0.72, "C6": 0.9})
    deltas = compute_feature_deltas(report, mode="marginal")
    c6_delta = next(d for d in deltas if d.config == "C6")
    assert c6_delta.reference == "C3"
    assert c6_delta.benchmark_pass_rate_delta == pytest.approx(0.2)


def test_compute_feature_deltas_marginal_skips_missing_configs():
    """Partial report (only C1 and C3) should not raise — just skip missing pairs."""
    report = _make_report({"C1": 0.5, "C3": 0.7})
    deltas = compute_feature_deltas(report, mode="marginal")
    # C3 vs C2 skipped (C2 missing), C5 vs C3 skipped (C5 missing), etc.
    assert all(d.config in report.summaries and d.reference in report.summaries for d in deltas)


# ---------------------------------------------------------------------------
# Group 3: compute_feature_deltas — cumulative mode
# ---------------------------------------------------------------------------

def test_compute_feature_deltas_cumulative_count():
    report = _make_report({"C1": 0.5, "C2": 0.6, "C3": 0.7, "C4": 0.75, "C5": 0.72, "C6": 0.8})
    deltas = compute_feature_deltas(report, mode="cumulative")
    assert len(deltas) == 5  # all configs except C1


def test_compute_feature_deltas_cumulative_baseline_is_c1():
    report = _make_report({"C1": 0.5, "C2": 0.6, "C3": 0.7, "C4": 0.75, "C5": 0.72, "C6": 0.8})
    deltas = compute_feature_deltas(report, mode="cumulative")
    assert all(d.reference == "C1" for d in deltas)


def test_compute_feature_deltas_cumulative_delta_value():
    report = _make_report({"C1": 0.5, "C2": 0.6, "C3": 0.7, "C4": 0.75, "C5": 0.72, "C6": 0.8})
    deltas = compute_feature_deltas(report, mode="cumulative")
    c6_delta = next(d for d in deltas if d.config == "C6")
    assert c6_delta.benchmark_pass_rate_delta == pytest.approx(0.3)


def test_compute_feature_deltas_raises_key_error_missing_baseline():
    report = _make_report({"C2": 0.6, "C3": 0.7})  # C1 absent
    with pytest.raises(KeyError, match="C1"):
        compute_feature_deltas(report, mode="cumulative", baseline="C1")


def test_compute_feature_deltas_raises_value_error_invalid_mode():
    report = _make_report({"C1": 0.5, "C6": 0.9})
    with pytest.raises(ValueError, match="mode"):
        compute_feature_deltas(report, mode="unknown")


# ---------------------------------------------------------------------------
# Group 4: AblationRunner
# ---------------------------------------------------------------------------

def test_ablation_runner_calls_compare_configs_once():
    mock_runner = MagicMock()
    mock_runner.compare_configs.return_value = _make_report(
        {"C1": 0.5, "C2": 0.6, "C3": 0.7, "C4": 0.75, "C5": 0.72, "C6": 0.8}
    )
    runner = AblationRunner(mock_runner)
    preset_configs = {p: {} for p in PRESET_SEQUENCE}
    runner.run(tasks=[], preset_configs=preset_configs)
    mock_runner.compare_configs.assert_called_once()


def test_ablation_runner_passes_correct_config_keys():
    mock_runner = MagicMock()
    mock_runner.compare_configs.return_value = _make_report({"C1": 0.5})
    runner = AblationRunner(mock_runner)
    preset_configs = {p: {"some": "config"} for p in PRESET_SEQUENCE}
    runner.run(tasks=[], preset_configs=preset_configs)
    passed_configs = mock_runner.compare_configs.call_args[0][1]
    assert set(passed_configs.keys()) == set(PRESET_SEQUENCE)


def test_ablation_runner_respects_presets_subset():
    mock_runner = MagicMock()
    mock_runner.compare_configs.return_value = _make_report({"C1": 0.5, "C3": 0.7, "C6": 0.9})
    runner = AblationRunner(mock_runner)
    preset_configs = {p: {} for p in PRESET_SEQUENCE}
    runner.run(tasks=[], preset_configs=preset_configs, presets=("C1", "C3", "C6"))
    passed_configs = mock_runner.compare_configs.call_args[0][1]
    assert set(passed_configs.keys()) == {"C1", "C3", "C6"}


def test_ablation_runner_skips_unknown_presets():
    """Presets not in preset_configs are silently skipped."""
    mock_runner = MagicMock()
    mock_runner.compare_configs.return_value = _make_report({"C1": 0.5})
    runner = AblationRunner(mock_runner)
    preset_configs = {"C1": {}}  # only C1 defined
    runner.run(tasks=[], preset_configs=preset_configs)
    passed_configs = mock_runner.compare_configs.call_args[0][1]
    assert set(passed_configs.keys()) == {"C1"}


# ---------------------------------------------------------------------------
# Group 5: write_ablation_report
# ---------------------------------------------------------------------------

def test_write_ablation_report_creates_file(tmp_path: Path):
    report = _make_report({"C1": 0.5, "C2": 0.6, "C3": 0.7, "C4": 0.75, "C5": 0.72, "C6": 0.9})
    marginal = compute_feature_deltas(report, mode="marginal")
    cumulative = compute_feature_deltas(report, mode="cumulative")
    path = write_ablation_report(
        marginal, cumulative, report,
        output_dir=tmp_path, benchmark="custom", version="v0.4.1",
    )
    assert path == tmp_path / "IMPROVEMENT_REPORT_v0.4.1.md"
    assert path.exists()


def test_write_ablation_report_contains_header(tmp_path: Path):
    report = _make_report({"C1": 0.5, "C6": 0.9})
    path = write_ablation_report(
        [], [], report,
        output_dir=tmp_path, benchmark="humaneval", version="v0.4.1",
    )
    content = path.read_text(encoding="utf-8")
    assert "# Ablation Report v0.4.1" in content
    assert "Benchmark: humaneval" in content
