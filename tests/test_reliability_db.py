#!/usr/bin/env python3
"""DB-free contract tests for signal-linked Phase 1 reliability reads."""

from __future__ import annotations

from datetime import date
import json
import sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src import dashboard  # noqa: E402
from src.execution import (  # noqa: E402
    EXECUTION_CONTRACT_VERSION,
    LEGACY_EXECUTION_CONTRACT_VERSION,
)


LABEL_CONFIG = {
    "label_mode": "triple_barrier",
    "horizon_days": 5,
    "tb_tp_atr": 1.5,
    "tb_sl_atr": 1.0,
    "tb_max_days": 5,
}


def _source_row(
    signal_id: int,
    run_date: str,
    *,
    model_version: str = "per-ticker-v1-20260718",
    model_kind: str = db.PHASE1_MODEL_KIND,
    label_config: dict | None = LABEL_CONFIG,
    prediction_id: int | None = None,
    linked_prediction_id: int | None = None,
    prediction_prob_up=0.7,
    conviction=0.65,
    prediction_horizon_days: int = 5,
    cs_rank=None,
    realized_ret=0.02,
) -> dict:
    if prediction_id is None and linked_prediction_id is None:
        # Default to a healthy direct link. Tests that need an absent link pass
        # prediction_id=None and linked_prediction_id=False explicitly below.
        prediction_id = 1000 + signal_id
        linked_prediction_id = prediction_id
    params = {"label_config": label_config} if label_config is not None else {}
    return {
        "signal_id": signal_id,
        "run_date": run_date,
        "ticker": f"{signal_id:04d}.JP",
        "prediction_id": prediction_id,
        "conviction": conviction,
        "entry_date": run_date,
        "realized_ret": realized_ret,
        "outcome_contract_version": EXECUTION_CONTRACT_VERSION,
        "linked_prediction_id": linked_prediction_id,
        "prediction_prob_up": prediction_prob_up,
        "model_version": model_version,
        "prediction_horizon_days": prediction_horizon_days,
        "cs_rank": cs_rank,
        "model_kind": model_kind,
        "model_params": params,
    }


def _fallback_row(signal_id: int, run_date: str, conviction=0.55) -> dict:
    row = _source_row(signal_id, run_date)
    row.update(
        {
            "prediction_id": None,
            "linked_prediction_id": None,
            "prediction_prob_up": None,
            "model_version": None,
            "prediction_horizon_days": None,
            "cs_rank": None,
            "model_kind": None,
            "model_params": None,
            "conviction": conviction,
        }
    )
    return row


def test_selects_linked_phase1_across_compatible_versions():
    rows = [
        _source_row(
            1,
            "2026-07-18",
            model_version="per-ticker-v1-20260718",
            prediction_prob_up=0.72,
        ),
        _source_row(
            2,
            "2026-07-11",
            model_version="per-ticker-v1-20260711",
            prediction_prob_up=0.61,
        ),
        # Same horizon but a different label definition must not be mixed.
        _source_row(
            3,
            "2026-07-04",
            model_version="per-ticker-v1-binary",
            label_config={"label_mode": "binary_1d", "horizon_days": 1},
            prediction_prob_up=0.58,
        ),
        # A newer active CS model must never become the reference or an input.
        _source_row(
            4,
            "2026-07-19",
            model_version="cs-v1-20260719",
            model_kind="cross_sectional_ranker_v1",
            prediction_prob_up=0.90,
            cs_rank=1,
        ),
        _fallback_row(5, "2026-07-10", conviction=0.55),
        # A present-but-invalid linked prediction cannot silently use conviction.
        _source_row(6, "2026-07-09", prediction_prob_up=None, conviction=0.99),
        _source_row(7, "2026-07-08", prediction_horizon_days=1),
    ]

    result = db._select_signal_reliability_rows(rows, horizon_days=5)
    selected = result["rows"]
    provenance = result["provenance"]

    assert [r["signal_id"] for r in selected] == [5, 2, 1]
    assert [r["probability_source"] for r in selected] == [
        "signals.conviction",
        "predictions.prob_up",
        "predictions.prob_up",
    ]
    assert provenance["phase"] == "phase1"
    assert provenance["source"] == "signals.prediction_id"
    assert provenance["candidate_signal_count"] == 7
    assert provenance["observation_count"] == 3
    assert provenance["linked_prediction_count"] == 2
    assert provenance["conviction_fallback_count"] == 1
    assert provenance["excluded_count"] == 4
    assert provenance["exclusions"] == {
        "incompatible_contract": 1,
        "missing_prediction_probability": 1,
        "non_phase1_prediction": 1,
        "prediction_horizon_mismatch": 1,
    }
    assert provenance["model_versions"] == [
        {"model_version": "per-ticker-v1-20260711", "count": 1},
        {"model_version": "per-ticker-v1-20260718", "count": 1},
    ]
    assert provenance["outcome_contract_versions"] == [EXECUTION_CONTRACT_VERSION]
    assert provenance["compatibility_contract"]["label_config"] == LABEL_CONFIG


def test_fallback_only_when_prediction_id_is_absent():
    rows = [
        _fallback_row(1, "2026-07-01", conviction=0.64),
        _fallback_row(2, "2026-07-02", conviction=None),
        _source_row(
            3,
            "2026-07-03",
            prediction_id=999,
            linked_prediction_id=None,
            conviction=0.88,
        ),
    ]
    result = db._select_signal_reliability_rows(rows, horizon_days=5)
    assert [r["signal_id"] for r in result["rows"]] == [1]
    assert result["provenance"]["conviction_fallback_count"] == 1
    assert result["provenance"]["exclusions"] == {
        "broken_prediction_link": 1,
        "missing_fallback_probability": 1,
    }


def test_missing_contract_fails_closed_to_one_model_version():
    rows = [
        _source_row(
            1,
            "2026-07-18",
            model_version="per-ticker-v1-20260718",
            model_kind=db.AUTO_STUB_MODEL_KIND,
            label_config=None,
        ),
        _source_row(
            2,
            "2026-07-11",
            model_version="per-ticker-v1-20260711",
            model_kind=db.AUTO_STUB_MODEL_KIND,
            label_config=None,
        ),
    ]
    result = db._select_signal_reliability_rows(rows, horizon_days=5)
    assert [r["signal_id"] for r in result["rows"]] == [1]
    assert result["provenance"]["exclusions"] == {"missing_compatibility_contract": 1}
    assert (
        result["provenance"]["compatibility_contract"]["compatibility_scope"]
        == "single_model_version_missing_contract"
    )


class FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self.rows = rows or []
        self.rowcount = rowcount
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.commits = 0

    def cursor(self, **_kwargs):
        return self.fake_cursor

    def commit(self):
        self.commits += 1

    def close(self):
        return None


def test_fetch_query_uses_direct_prediction_link_not_active_model():
    row = _source_row(1, "2026-07-18")
    row["run_date"] = date(2026, 7, 18)
    row["entry_date"] = date(2026, 7, 21)
    cursor = FakeCursor([row])
    result = db.fetch_signal_reliability_rows(
        FakeConn(cursor), horizon_days=5, history_days=180
    )

    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split()).lower()
    assert "left join predictions p on p.id = s.prediction_id" in normalized
    assert "model_registry" in normalized
    assert "active" not in normalized
    assert params == {
        "h": 5,
        "d": 180,
        "contract": EXECUTION_CONTRACT_VERSION,
    }
    assert result["rows"][0]["run_date"] == "2026-07-18"
    assert result["rows"][0]["entry_date"] == "2026-07-21"


def test_drift_outcomes_use_signal_prediction_and_current_execution_contract():
    cursor = FakeCursor(
        [
            {
                "ticker": "7011.JP",
                "prob_up": 0.7,
                "raw_score": 0.4,
                "realized_ret": 0.02,
                "hit": True,
            }
        ]
    )
    rows = db.fetch_prediction_outcomes(FakeConn(cursor), "per-ticker-v1-20260718", 5)

    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split()).lower()
    assert "from signals s join predictions p on p.id = s.prediction_id" in normalized
    assert "s.run_date = p.run_date" not in normalized
    assert "s.ticker = p.ticker" not in normalized
    assert "o.contract_version = %(contract)s" in normalized
    assert params == {
        "horizon": 5,
        "version": "per-ticker-v1-20260718",
        "contract": EXECUTION_CONTRACT_VERSION,
    }
    assert rows[0]["ticker"] == "7011.JP"


def test_drift_outcomes_pool_across_model_lineage_by_kind():
    # Weekly retrains rotate model_version, so pooling must key on the
    # registry kind (lineage) — not the exact active version — while still
    # excluding cross-sectional predictions and legacy versions.
    cursor = FakeCursor(
        [
            {
                "ticker": "7011.JP",
                "prob_up": 0.7,
                "raw_score": 0.4,
                "realized_ret": 0.02,
                "hit": True,
            }
        ]
    )
    rows = db.fetch_prediction_outcomes_for_kind(
        FakeConn(cursor), db.PHASE1_MODEL_KIND, 5
    )

    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split()).lower()
    assert "join predictions p on p.id = s.prediction_id" in normalized
    assert "join model_registry mr on mr.version = p.model_version" in normalized
    assert "mr.kind = %(kind)s" in normalized
    assert "p.cs_rank is null" in normalized
    assert "o.contract_version = %(contract)s" in normalized
    assert "model_version = %(version)s" not in normalized
    assert params == {
        "horizon": 5,
        "kind": db.PHASE1_MODEL_KIND,
        "contract": EXECUTION_CONTRACT_VERSION,
    }
    assert rows[0]["ticker"] == "7011.JP"


def test_link_prediction_ids_rejects_later_inserted_cs_prediction():
    # Reproduces the dangerous ordering: Phase 1 id=10, then CS id=11.
    inserted = [
        {
            "linked_prediction_id": 10,
            "model_version": "per-ticker-v1-20260718",
            "model_kind": db.PHASE1_MODEL_KIND,
            "cs_rank": None,
        },
        {
            "linked_prediction_id": 11,
            "model_version": "cs-v1-20260718",
            "model_kind": "cross_sectional_ranker_v1",
            "cs_rank": 1,
        },
    ]
    assert db._is_phase1_prediction_row(
        {
            "model_version": "ephemeral-phase1-v3-artifact-gate",
            "model_kind": db.AUTO_STUB_MODEL_KIND,
            "cs_rank": None,
        }
    )
    assert not db._is_phase1_prediction_row(
        {
            "model_version": "ephemeral-phase1-v3-artifact-gate",
            "model_kind": "cross_sectional_ranker_v1",
            "cs_rank": None,
        }
    )
    phase1_ids = [
        row["linked_prediction_id"]
        for row in inserted
        if db._is_phase1_prediction_row(row)
    ]
    assert max(phase1_ids) == 10

    cursor = FakeCursor(rowcount=1)
    conn = FakeConn(cursor)
    assert db._link_prediction_ids(conn) == 1
    sql, params = cursor.executed[0]
    normalized = " ".join(sql.split()).lower()
    assert "distinct on (p.run_date, p.ticker)" in normalized
    assert "p.cs_rank is null" in normalized
    assert "join model_registry mr" in normalized
    assert "order by p.run_date, p.ticker, p.id desc" in normalized
    assert db.PHASE1_MODEL_KIND in params
    assert db.db_records.LEGACY_MODEL_VERSION in params
    assert f"{db.EPHEMERAL_PHASE1_MODEL_VERSION_PREFIX}%" in params
    assert conn.commits == 1


def test_outcome_detail_includes_and_normalizes_eval_date():
    cursor = FakeCursor(
        [
            {
                "entry_date": date(2026, 7, 21),
                "eval_date": date(2026, 7, 28),
                "ticker": "7011.JP",
            }
        ]
    )
    rows = db.fetch_outcome_detail_rows(
        FakeConn(cursor), horizon_days=5, history_days=180
    )
    sql, _ = cursor.executed[0]
    assert "o.eval_date" in sql
    assert "o.contract_version = %(contract)s" in sql
    assert rows[0]["entry_date"] == "2026-07-21"
    assert rows[0]["eval_date"] == "2026-07-28"


def test_upsert_outcome_persists_execution_contract_columns():
    cursor = FakeCursor()
    conn = FakeConn(cursor)
    payload = {
        "market_as_of_date": "2026-07-17",
        "entry_date": "2026-07-21",
        "eval_date": "2026-07-27",
        "entry_close": 101.0,
        "exit_close": 105.0,
        "entry_price": 101.0,
        "exit_price": 105.0,
        "entry_price_basis": "next_session_open",
        "exit_price_basis": "horizon_session_close",
        "contract_version": EXECUTION_CONTRACT_VERSION,
        "benchmark_basis": "unavailable_same_basis",
        "realized_ret": 105 / 101 - 1,
        "benchmark_ret": None,
        "excess_ret": None,
        "hit": True,
        "mae": -0.01,
        "mfe": 0.05,
        "exit_reason": "time",
    }
    db.upsert_outcome(conn, signal_id=42, horizon_days=5, payload=payload)
    sql, params = cursor.executed[0]
    for column in (
        "market_as_of_date",
        "entry_price",
        "exit_price",
        "entry_price_basis",
        "exit_price_basis",
        "contract_version",
        "benchmark_basis",
    ):
        assert column in sql
        assert params[column] == payload[column]
    assert conn.commits == 1


def test_fetch_unsettled_counts_only_current_contract_rows():
    cursor = FakeCursor(
        [
            {
                "signal_id": 1,
                "ticker": "7011.JP",
                "as_of_date": date(2026, 7, 17),
                "action": "BUY",
                "settled": [1],
            }
        ]
    )
    rows = db.fetch_unsettled(FakeConn(cursor))
    sql, params = cursor.executed[0]
    assert "o.contract_version = %s" in sql
    assert params == (EXECUTION_CONTRACT_VERSION,)
    assert rows[0]["missing_horizons"] == [5, 10]


def test_fetch_signals_for_restatement_returns_all_actionable():
    cursor = FakeCursor(
        [
            {
                "signal_id": 1,
                "ticker": "7011.JP",
                "as_of_date": date(2026, 7, 17),
                "action": "BUY",
            }
        ]
    )
    rows = db.fetch_signals_for_outcome_restatement(FakeConn(cursor))
    sql, _ = cursor.executed[0]
    assert "s.action IN ('BUY','MILD_BUY','SELL','MILD_SELL')" in sql
    assert rows[0]["as_of_date"] == "2026-07-17"


def test_benchmark_refill_query_targets_current_contract():
    cursor = FakeCursor([])
    rows = db.fetch_outcomes_missing_benchmark(FakeConn(cursor))
    sql, params = cursor.executed[0]
    assert rows == []
    assert "contract_version = %s" in sql
    assert params == (EXECUTION_CONTRACT_VERSION,)
    assert params != (LEGACY_EXECUTION_CONTRACT_VERSION,)


def test_dashboard_exports_reliability_provenance_without_active_lookup():
    original_path = dashboard.PERFORMANCE_DETAIL_FILE
    original_db_enabled = dashboard.db.db_enabled
    original_connect = dashboard.db.connect
    original_fetch_detail = dashboard.db.fetch_outcome_detail_rows
    original_fetch_reliability = dashboard.db.fetch_signal_reliability_rows
    original_active_lookup = dashboard.db.active_model_version
    original_builder = dashboard.performance.build_performance_detail

    provenance = {
        "phase": "phase1",
        "source": "signals.prediction_id",
        "observation_count": 2,
        "linked_prediction_count": 1,
        "conviction_fallback_count": 1,
        "excluded_count": 0,
        "exclusions": {},
        "model_versions": [{"model_version": "per-ticker-v1-20260718", "count": 1}],
    }

    def active_lookup_must_not_run(_conn):
        raise AssertionError("generic active model lookup must not be used")

    with tempfile.TemporaryDirectory() as tmp:
        try:
            dashboard.PERFORMANCE_DETAIL_FILE = Path(tmp) / "performance_detail.json"
            dashboard.db.db_enabled = lambda: True
            dashboard.db.connect = lambda: FakeConn(FakeCursor())
            dashboard.db.fetch_outcome_detail_rows = lambda *_args, **_kwargs: [
                {"entry_date": "2026-07-21", "realized_ret": 0.02}
            ]
            dashboard.db.fetch_signal_reliability_rows = lambda *_args, **_kwargs: {
                "rows": [{"prob_up": 0.7, "realized_ret": 0.02}],
                "provenance": provenance,
            }
            dashboard.db.active_model_version = active_lookup_must_not_run
            dashboard.performance.build_performance_detail = lambda *_args, **_kwargs: {
                "horizon_days": 5,
                "history_days": 180,
                "equity_curve": [],
                "drawdown_curve": [],
                "rolling": {},
                "benchmark_coverage": {"reason": "unavailable_same_basis"},
                "reliability": {"brier": 0.09, "bins": []},
            }

            dashboard.export_performance_detail()
            payload = json.loads(
                dashboard.PERFORMANCE_DETAIL_FILE.read_text(encoding="utf-8")
            )
            assert payload["available"] is True
            assert payload["reliability"]["provenance"] == provenance
            assert payload["benchmark_unavailable_reason"] == "unavailable_same_basis"
        finally:
            dashboard.PERFORMANCE_DETAIL_FILE = original_path
            dashboard.db.db_enabled = original_db_enabled
            dashboard.db.connect = original_connect
            dashboard.db.fetch_outcome_detail_rows = original_fetch_detail
            dashboard.db.fetch_signal_reliability_rows = original_fetch_reliability
            dashboard.db.active_model_version = original_active_lookup
            dashboard.performance.build_performance_detail = original_builder


ALL_TESTS = [
    test_selects_linked_phase1_across_compatible_versions,
    test_fallback_only_when_prediction_id_is_absent,
    test_missing_contract_fails_closed_to_one_model_version,
    test_fetch_query_uses_direct_prediction_link_not_active_model,
    test_drift_outcomes_use_signal_prediction_and_current_execution_contract,
    test_drift_outcomes_pool_across_model_lineage_by_kind,
    test_link_prediction_ids_rejects_later_inserted_cs_prediction,
    test_outcome_detail_includes_and_normalizes_eval_date,
    test_upsert_outcome_persists_execution_contract_columns,
    test_fetch_unsettled_counts_only_current_contract_rows,
    test_fetch_signals_for_restatement_returns_all_actionable,
    test_benchmark_refill_query_targets_current_contract,
    test_dashboard_exports_reliability_provenance_without_active_lookup,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
