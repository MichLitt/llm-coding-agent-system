# test_report.py — do NOT modify this file
# After refactoring, all imports and behaviour must still work.
import json
import pytest
from datetime import date
from report_generator import ReportGenerator, ReportConfig, SalesRecord, ReportData


SAMPLE_ROWS = [
    {"date": "2024-01-01", "product": "Widget", "region": "North", "units": 10, "revenue": 500.0, "cost": 300.0},
    {"date": "2024-01-01", "product": "Gadget", "region": "South", "units": 5, "revenue": 250.0, "cost": 100.0},
    {"date": "2024-01-02", "product": "Widget", "region": "South", "units": 8, "revenue": 400.0, "cost": 240.0},
    {"date": "2024-01-03", "product": "Gizmo", "region": "North", "units": 3, "revenue": 150.0, "cost": 90.0},
    {"date": "2024-01-03", "product": "Widget", "region": "East", "units": 20, "revenue": 1000.0, "cost": 600.0},
]


def make_gen(**kwargs):
    return ReportGenerator(ReportConfig(**kwargs)).from_dicts(SAMPLE_ROWS)


def test_generate_text_output():
    out = make_gen().generate()
    assert "Sales Report" in out
    assert "Total Revenue" in out


def test_generate_json_output():
    out = make_gen(output_format="json").generate()
    data = json.loads(out)
    assert "summary" in data
    assert data["summary"]["total_revenue"] == pytest.approx(2300.0)


def test_generate_csv_output():
    out = make_gen(output_format="csv").generate()
    lines = out.strip().splitlines()
    assert lines[0].startswith("date,product")
    assert len(lines) == 6  # header + 5 rows


def test_date_filtering():
    out = make_gen(
        output_format="json",
        date_from=date(2024, 1, 2),
        date_to=date(2024, 1, 2),
    ).generate()
    data = json.loads(out)
    assert len(data["records"]) == 1


def test_group_by_product():
    out = make_gen(output_format="text", group_by="product").generate()
    assert "PRODUCT" in out


def test_total_profit():
    out = make_gen(output_format="json").generate()
    data = json.loads(out)
    assert data["summary"]["total_profit"] == pytest.approx(1070.0)


def test_sales_record_profit():
    r = SalesRecord(date=date.today(), product="X", region="Y", units=1, revenue=100, cost=60)
    assert r.profit == pytest.approx(40.0)
    assert r.margin == pytest.approx(0.4)


def test_report_data_totals():
    rows = [
        SalesRecord(date.today(), "A", "R1", 1, 100.0, 60.0),
        SalesRecord(date.today(), "B", "R2", 2, 200.0, 100.0),
    ]
    rd = ReportData(records=rows)
    assert rd.total_revenue() == pytest.approx(300.0)
    assert rd.total_units() == 3


def test_no_data_raises():
    gen = ReportGenerator()
    with pytest.raises(RuntimeError, match="No data loaded"):
        gen.generate()
