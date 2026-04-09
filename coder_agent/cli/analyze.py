import json

import click

from coder_agent.config import cfg
from coder_agent.eval.analysis import TrajectoryAnalyzer


@click.command(name="analyze")
@click.argument("experiment_id")
@click.option("--compare", default=None, help="Comma-separated experiment IDs to compare")
@click.option("--llm-taxonomy", is_flag=True, help="Use LLM-as-Critic for two-dimensional failure classification")
def analyze_command(experiment_id: str, compare: str | None, llm_taxonomy: bool) -> None:
    analyzer = TrajectoryAnalyzer()

    if compare:
        ids = [experiment_id] + [s.strip() for s in compare.split(",")]
        analyzer.compare_experiments(ids)
        return

    manifest_path = cfg.eval.output_dir / f"{experiment_id}_comparison_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        analyzer.compare_experiments(manifest.get("experiments", []))
        return

    stats = analyzer.compute_statistics(experiment_id)
    taxonomy = analyzer.failure_taxonomy(experiment_id)
    analyzer.print_report(stats, taxonomy)
    layered_report = analyzer.layered_failure_report(experiment_id)
    analyzer.print_layered_report(layered_report)
    report_path = analyzer.write_analysis_report(experiment_id)
    click.echo(f"\nWrote layered analysis report: {report_path}")

    if llm_taxonomy:
        click.echo("\nRunning LLM-as-Critic classification (this may take a moment)...")
        llm_results = analyzer.failure_taxonomy_llm(experiment_id)
        analyzer.print_llm_taxonomy(llm_results)
