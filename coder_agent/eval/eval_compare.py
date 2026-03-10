import json
from dataclasses import asdict
from pathlib import Path

from coder_agent.eval.metrics import MetricsSummary


def write_comparison_report(
    output_dir: Path,
    *,
    report_label: str,
    configs: dict[str, dict],
    summaries: dict[str, MetricsSummary],
) -> None:
    report_stem = f"{report_label}_comparison_report.json" if report_label else "comparison_report.json"
    report_path = output_dir / report_stem
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {label: asdict(summary) for label, summary in summaries.items()},
            handle,
            indent=2,
        )

    if not report_label:
        return

    manifest_path = output_dir / f"{report_label}_comparison_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "compare_label": report_label,
                "experiments": list(configs.keys()),
                "report_path": str(report_path),
            },
            handle,
            indent=2,
        )
