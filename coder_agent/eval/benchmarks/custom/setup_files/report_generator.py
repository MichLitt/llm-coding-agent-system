# report_generator.py — 350-line god class; agent must split into 3 focused modules
# while keeping test_report.py passing.
#
# The agent should split into:
#   - report_models.py  (ReportData, SalesRecord, ReportConfig data classes)
#   - report_formatter.py  (formatting and rendering logic)
#   - report_generator.py  (ReportGenerator orchestrator — keeps this name)

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Any, Optional


@dataclass
class SalesRecord:
    date: date
    product: str
    region: str
    units: int
    revenue: float
    cost: float

    @property
    def profit(self) -> float:
        return self.revenue - self.cost

    @property
    def margin(self) -> float:
        if self.revenue == 0:
            return 0.0
        return self.profit / self.revenue


@dataclass
class ReportConfig:
    title: str = "Sales Report"
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    group_by: str = "region"         # "region" | "product" | "date"
    include_charts: bool = False
    output_format: str = "text"       # "text" | "csv" | "json"
    top_n: int = 5


@dataclass
class ReportData:
    records: list[SalesRecord] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)

    def filter_by_date(self, date_from: Optional[date], date_to: Optional[date]) -> "ReportData":
        filtered = self.records
        if date_from:
            filtered = [r for r in filtered if r.date >= date_from]
        if date_to:
            filtered = [r for r in filtered if r.date <= date_to]
        return ReportData(records=filtered, generated_at=self.generated_at)

    def group_by_region(self) -> dict[str, list[SalesRecord]]:
        groups: dict[str, list[SalesRecord]] = {}
        for r in self.records:
            groups.setdefault(r.region, []).append(r)
        return groups

    def group_by_product(self) -> dict[str, list[SalesRecord]]:
        groups: dict[str, list[SalesRecord]] = {}
        for r in self.records:
            groups.setdefault(r.product, []).append(r)
        return groups

    def group_by_date(self) -> dict[date, list[SalesRecord]]:
        groups: dict[date, list[SalesRecord]] = {}
        for r in self.records:
            groups.setdefault(r.date, []).append(r)
        return groups

    def total_revenue(self) -> float:
        return sum(r.revenue for r in self.records)

    def total_cost(self) -> float:
        return sum(r.cost for r in self.records)

    def total_profit(self) -> float:
        return sum(r.profit for r in self.records)

    def total_units(self) -> int:
        return sum(r.units for r in self.records)

    def average_margin(self) -> float:
        if not self.records:
            return 0.0
        return sum(r.margin for r in self.records) / len(self.records)


class ReportFormatter:
    """Formats ReportData into text, CSV, or JSON output."""

    def format_text(self, data: ReportData, config: ReportConfig) -> str:
        lines = [
            f"{'=' * 60}",
            f"  {config.title}",
            f"  Generated: {data.generated_at.strftime('%Y-%m-%d %H:%M')}",
            f"{'=' * 60}",
            "",
            f"SUMMARY",
            f"  Total Revenue:  ${data.total_revenue():>12,.2f}",
            f"  Total Cost:     ${data.total_cost():>12,.2f}",
            f"  Total Profit:   ${data.total_profit():>12,.2f}",
            f"  Total Units:    {data.total_units():>13,}",
            f"  Avg Margin:     {data.average_margin() * 100:>12.1f}%",
            "",
        ]

        groups = self._get_groups(data, config.group_by)
        lines.append(f"BREAKDOWN BY {config.group_by.upper()}")

        summaries = []
        for key, records in groups.items():
            sub = ReportData(records=records)
            summaries.append((key, sub.total_revenue(), sub.total_profit()))

        summaries.sort(key=lambda x: x[1], reverse=True)
        for i, (key, rev, profit) in enumerate(summaries[: config.top_n]):
            lines.append(f"  {i+1:2d}. {str(key):<25} Rev: ${rev:>10,.2f}  Profit: ${profit:>10,.2f}")

        return "\n".join(lines)

    def format_csv(self, data: ReportData, config: ReportConfig) -> str:
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["date", "product", "region", "units", "revenue", "cost", "profit", "margin"])
        for r in data.records:
            writer.writerow([
                r.date.isoformat(), r.product, r.region,
                r.units, f"{r.revenue:.2f}", f"{r.cost:.2f}",
                f"{r.profit:.2f}", f"{r.margin:.4f}"
            ])
        return buf.getvalue()

    def format_json(self, data: ReportData, config: ReportConfig) -> str:
        payload = {
            "title": config.title,
            "generated_at": data.generated_at.isoformat(),
            "summary": {
                "total_revenue": data.total_revenue(),
                "total_cost": data.total_cost(),
                "total_profit": data.total_profit(),
                "total_units": data.total_units(),
                "average_margin": data.average_margin(),
            },
            "records": [
                {
                    "date": r.date.isoformat(),
                    "product": r.product,
                    "region": r.region,
                    "units": r.units,
                    "revenue": r.revenue,
                    "cost": r.cost,
                    "profit": r.profit,
                    "margin": r.margin,
                }
                for r in data.records
            ],
        }
        return json.dumps(payload, indent=2)

    def _get_groups(self, data: ReportData, group_by: str) -> dict:
        if group_by == "region":
            return data.group_by_region()
        elif group_by == "product":
            return data.group_by_product()
        elif group_by == "date":
            return data.group_by_date()
        raise ValueError(f"Unknown group_by: {group_by!r}")


class ReportLoader:
    """Loads SalesRecord data from CSV files."""

    def load_csv(self, path: str | Path) -> ReportData:
        records = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(SalesRecord(
                    date=date.fromisoformat(row["date"]),
                    product=row["product"],
                    region=row["region"],
                    units=int(row["units"]),
                    revenue=float(row["revenue"]),
                    cost=float(row["cost"]),
                ))
        return ReportData(records=records)

    def load_dicts(self, rows: list[dict[str, Any]]) -> ReportData:
        records = []
        for row in rows:
            records.append(SalesRecord(
                date=date.fromisoformat(row["date"]) if isinstance(row["date"], str) else row["date"],
                product=row["product"],
                region=row["region"],
                units=int(row["units"]),
                revenue=float(row["revenue"]),
                cost=float(row["cost"]),
            ))
        return ReportData(records=records)


class ReportGenerator:
    """Orchestrates loading, filtering, and formatting of sales reports."""

    def __init__(self, config: Optional[ReportConfig] = None):
        self.config = config or ReportConfig()
        self._loader = ReportLoader()
        self._formatter = ReportFormatter()

    def from_csv(self, path: str | Path) -> "ReportGenerator":
        self._data = self._loader.load_csv(path)
        return self

    def from_dicts(self, rows: list[dict]) -> "ReportGenerator":
        self._data = self._loader.load_dicts(rows)
        return self

    def generate(self) -> str:
        if not hasattr(self, "_data"):
            raise RuntimeError("No data loaded. Call from_csv() or from_dicts() first.")

        data = self._data.filter_by_date(self.config.date_from, self.config.date_to)

        if self.config.output_format == "csv":
            return self._formatter.format_csv(data, self.config)
        elif self.config.output_format == "json":
            return self._formatter.format_json(data, self.config)
        else:
            return self._formatter.format_text(data, self.config)

    def save(self, output_path: str | Path) -> None:
        content = self.generate()
        Path(output_path).write_text(content, encoding="utf-8")
