"""D2 audit-ledger tests.

Each test gets a fresh tmp data_dir (its own SQLite file), so chains never
bleed across tests. Covers: empty chain verifies, appends verify in order,
and mutating any single stored row breaks verification."""

"""D2 audit-ledger tests: empty chain verifies, appends verify in order,
any single-row tamper breaks verification. Fresh tmp data_dir per test."""

from itertools import pairwise

import pytest

from app.audit.ledger import GENESIS_HASH, append, iter_rows
from app.audit.verify_chain import verify_chain
from app.db import get_conn, init_db
from app.settings import get_settings


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Fresh ledger in a per-test tmp data_dir — isolation by construction."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    with get_conn() as conn:
        init_db(conn)
        yield conn


def _row_kwargs(n: int) -> dict:
    return {
        "subscription_id": f"sub_TEST{n:04d}",
        "trigger_event": "subscription.halted",
        "policy_eval": {"decision": "allowed", "reason": f"rule-run-{n}", "caps": {"attempt": n}},
        "human_gate": False,
        "rzp_call": None,
        "outcome": "ACTION_SENT",
        "recovered_paise": 0,
        "mode": "NORMAL",
    }


class TestEmptyChain:
    def test_empty_chain_verifies(self, db):
        rows = list(iter_rows(db))
        assert rows == []
        ok, detail = verify_chain(rows)
        assert ok, detail

    def test_verify_handles_generator_input(self, db):
        ok, detail = verify_chain(iter_rows(db))
        assert ok, detail


class TestAppendAndVerify:
    def test_five_appends_verify_in_order(self, db):
        appended = [append(db, **_row_kwargs(n)) for n in range(5)]

        rows = list(iter_rows(db))
        ok, detail = verify_chain(rows)
        assert ok, detail

        # Append order is preserved and identity fields round-trip.
        assert [r["action_id"] for r in rows] == [a["action_id"] for a in appended]
        assert [r["subscription_id"] for r in rows] == [f"sub_TEST{n:04d}" for n in range(5)]

        # Chain structure: genesis link, then each row links to its predecessor.
        assert rows[0]["prev_hash"] == GENESIS_HASH
        for prev, cur in pairwise(rows):
            assert cur["prev_hash"] == prev["row_hash"]

    def test_complex_fields_round_trip_exactly(self, db):
        append(
            db,
            **_row_kwargs(0)
            | {
                "policy_eval": {"decision": "queue_human", "reason": "amount>500"},
                "features": {"attempts": 2, "outstanding_paise": 75_000},
                "score": 0.75,
                "human_gate": True,
                "rzp_call": {"method": "POST", "path": "/payment_links", "status": 201},
            },
        )
        (row,) = list(iter_rows(db))
        assert row["policy_eval"] == {"decision": "queue_human", "reason": "amount>500"}
        assert row["features"] == {"attempts": 2, "outstanding_paise": 75_000}
        assert row["score"] == 0.75
        assert row["human_gate"] is True
        assert row["rzp_call"] == {
            "method": "POST",
            "path": "/payment_links",
            "status": 201,
        }

    def test_nullable_fields_stay_none(self, db):
        append(db, **_row_kwargs(0))
        (row,) = list(iter_rows(db))
        assert row["score"] is None
        assert row["features"] is None
        assert row["llm_request_hash"] is None
        assert row["llm_output_raw"] is None
        assert row["llm_model"] is None
        assert row["rzp_call"] is None


class TestTamperDetection:
    @pytest.mark.parametrize(
        ("seq", "tamper_sql", "tamper_args"),
        [
            (1, "SET outcome = 'ACTION_FORGED' WHERE seq = 1", ()),  # first row
            (2, "SET recovered_paise = recovered_paise + 1 WHERE seq = 2", ()),  # middle
            (3, "SET policy_eval = replace(policy_eval, 'rule', 'RULE') WHERE seq = 3", ()),
            # hash field — 'x' is never a hex digit, so the tamper is a real
            # mutation no matter what the stored hash starts with (flipping
            # to '0' was a no-op 1/16 of the time).
            (3, "SET row_hash = 'x' || substr(row_hash, 2) WHERE seq = 3", ()),
        ],
        ids=["first-row", "middle-row-amount", "middle-row-policy", "hash-field"],
    )
    def test_mutating_any_row_breaks_verification(self, db, seq, tamper_sql, tamper_args):
        for n in range(3):
            append(db, **_row_kwargs(n))
        assert verify_chain(list(iter_rows(db)))[0] is True  # sane before tamper

        db.execute(f"UPDATE audit_ledger {tamper_sql}", *tamper_args)
        ok, detail = verify_chain(list(iter_rows(db)))
        assert ok is False
        assert f"row {seq}" in detail

    def test_deleting_a_row_breaks_verification(self, db):
        for n in range(3):
            append(db, **_row_kwargs(n))
        db.execute("DELETE FROM audit_ledger WHERE seq = 2")
        ok, _ = verify_chain(list(iter_rows(db)))
        assert ok is False


class TestFreshChainIsolation:
    def test_separate_data_dirs_get_independent_chains(self, monkeypatch, tmp_path):
        s = get_settings()

        monkeypatch.setattr(s, "data_dir", tmp_path / "chain_a")
        with get_conn() as conn:
            init_db(conn)
            append(conn, **_row_kwargs(1))
            append(conn, **_row_kwargs(2))
            head_hash_a = list(iter_rows(conn))[-1]["row_hash"]

        monkeypatch.setattr(s, "data_dir", tmp_path / "chain_b")
        with get_conn() as conn:
            init_db(conn)
            assert list(iter_rows(conn)) == []  # chain A is invisible here

            fresh = append(conn, **_row_kwargs(0))
            assert fresh["prev_hash"] == GENESIS_HASH  # new chain starts at genesis
            assert fresh["row_hash"] != head_hash_a
            ok, detail = verify_chain(list(iter_rows(conn)))
            assert ok, detail
