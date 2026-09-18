# SWE-12 Candidate Audit

Status: promotion evidence complete. This is not a model baseline result.

## Protocol

Candidates are sourced from `princeton-nlp/SWE-bench_Lite` at revision
`6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`. Each promotion candidate must
prove, three times from a fresh checkout, that:

1. its pinned base commit checks out and its declared environment installs;
2. the official regression test fails after applying only `test_patch`;
3. the same test passes after applying official `patch` and `test_patch`.

Pass-to-pass coverage and review of the final execution overrides are completed;
the four tasks are included in the accepted 12-task SWE promoted lane.

Run the candidate replay contract with:

```bash
uv run python scripts/audit_swebench_promotion_candidates.py \
  --repeats 3 \
  --output artifacts/swe-promotion-audit/results.json
```

The script consumes the independent candidate snapshot and overrides file. It
is a promotion-audit tool; the accepted source and manifest are updated only
after an audit result is reviewed.

The three-repeat artifact at
`artifacts/swe-promotion-audit/three-repeat-results.json` records a successful
clean replay for all four candidates. The follow-up artifact
`artifacts/swe-promotion-audit/three-repeat-with-p2p-results.json` establishes
three complete F2P/P2P replays for Pylint, Requests, and Xarray. Seaborn's
compatible-environment follow-up is recorded in
`seaborn-p2p-compatible-results.json`.

## Results

| Candidate | Repository | Environment | Buggy fail | Gold pass | Repeats | Promotion state |
| --- | --- | --- | --- | --- | ---: | --- |
| `mwaskom__seaborn-3407` | `mwaskom/seaborn` | Python 3.10; editable install + pytest + `matplotlib<3.8`, `numpy<2` | yes | yes | 3/3 | promoted |
| `pylint-dev__pylint-6506` | `pylint-dev/pylint` | Python 3.10; editable install + pytest | yes | yes | 3/3 | promoted |
| `psf__requests-3362` | `psf/requests` | Python 3.9; editable install + `urllib3==1.26.18`, `chardet==4.0.0` | yes | yes | 3/3 | promoted |
| `pydata__xarray-5131` | `pydata/xarray` | Python 3.9; editable install + `setuptools<81`, `numpy<2`, `pandas<2` | yes | yes | 3/3 | promoted |

The Seaborn checks used the official target
`tests/test_axisgrid.py::TestPairGrid::test_pairplot_column_multiindex`. The
buggy state consistently raised the expected `KeyError`; the official patch
made the test pass. Package warnings were non-fatal and did not alter the
test outcome.

The rejected Astropy 5.2 candidate could not complete its editable build on the local macOS/arm64
environment. Pinning `setuptools<70`, `numpy<2`, `extension-helpers`, and
disabling build isolation resolved the initial metadata failures but not the
native build: the checkout lacks `astropy/table/_np_utils.c`. This candidate is
therefore excluded under the plan's platform-stability rule, rather than being
counted as an Agent or model failure.

The rejected Matplotlib 3.7 replacement likewise could not build its bundled
FreeType 2.6 source with the local arm64 toolchain. It is excluded for the same
platform-stability reason and was replaced by a Pylint 2.14 candidate from the
same locked official dataset revision.

Pylint 2.14 completed all three clean replays with its official config tests
and PASS_TO_PASS coverage before promotion.

Requests 2.10 is incompatible with Python 3.10 because it imports
`collections.MutableMapping`; Python 3.9 is therefore part of its proposed
environment pin. Its first clean replay passed with the explicit urllib3 and
chardet compatibility pins. All three fresh-checkout repetitions and official
PASS_TO_PASS coverage passed before promotion.

Xarray 0.17 needs `setuptools<81` for `pkg_resources`, plus NumPy 1.x and
Pandas 1.x compatibility pins. In the first fresh checkout, all eight official
`test_groupby_repr` parameterizations failed in the buggy state and all passed
with the official gold patch. All three repetitions and official PASS_TO_PASS
coverage passed before promotion.
