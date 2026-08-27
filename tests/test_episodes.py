"""D2 episode state-machine tests.

Fresh tmp data_dir per test (its own SQLite file) — episodes, ledger rows
and chain state never bleed across tests. Covers: the legal linear path
(exactly 7 ledger rows, chain verifies), transition+ledger atomicity
(nothing partial ever persists), illegal transitions (raise, write nothing),
stop-on-charge/cancel voiding (reason stamped, terminal after, replay-
idempotent), and creation idempotency under replayed halts."""

import pytest

from app.audit.ledger import iter_rows
from app.audit.verify_chain import verify_chain
from app.core.episodes import (
    EPISODE_STATES,
    EpisodeNotFoundError,
    TransitionError,
    create_episode,
    get_episode,
    get_open_episodes,
    transition,
    void_open_episodes,
)
from app.db import get_conn, init_db
from app.settings import get_settings

HALT_TS = "2026-08-28T10:00:00+00:00"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """Fresh episodes+ledger store in a per-test tmp data_dir."""
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    with get_conn() as conn:
        init_db(conn)
        yield conn


def _make(db, subscription_id: str, states: tuple[str, ...] = ("DIAGNOSED", "SCORED", "GATED", "SENT")) -> dict:
    """Episode driven through `states` from NEW by the legal path."""
    ep = create_episode(db, subscription_id=subscription_id, halt_ts_utc=HALT_TS, cohort="TREATMENT")
    for state in states:
        ep = transition(db, ep["id"], state)
    return ep


def _make_sent(db, subscription_id: str) -> dict:
    return _make(db, subscription_id)


class TestCreate:
    def test_halt_creates_new_episode(self, db):
        ep = create_episode(db, subscription_id="sub_NEW1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        assert ep["state"] == "NEW"
        assert ep["subscription_id"] == "sub_NEW1"
        assert ep["halt_ts_utc"] == HALT_TS
        assert ep["attempt_count"] == 0
        assert ep["last_action_ts_utc"] is None
        assert ep["void_reason"] is None
        assert ep["cohort"] == "TREATMENT"

    def test_cohort_defaults_to_cohorts_table_lookup(self, db):
        db.execute(
            "INSERT INTO cohorts (subscription_id, cohort, slot, created_utc) "
            "VALUES (?, 'TREATMENT', 1, ?)",
            ("sub_COH1", HALT_TS),
        )
        ep = create_episode(db, subscription_id="sub_COH1", halt_ts_utc=HALT_TS)
        assert ep["cohort"] == "TREATMENT"

    def test_duplicate_halt_is_idempotent(self, db):
        first = create_episode(db, subscription_id="sub_DUP1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        again = create_episode(db, subscription_id="sub_DUP1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        assert again["id"] == first["id"]
        assert again["state"] == "NEW"
        n = db.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()["c"]
        assert n == 1
        # The replay added nothing: one creation ledger row, never a second.
        assert [r["outcome"] for r in iter_rows(db)] == ["EPISODE_CREATED"]

    def test_halt_after_terminal_episode_starts_new_cycle(self, db):
        first = _make(db, "sub_CYCLE1", states=("DIAGNOSED", "SCORED", "GATED", "SENT", "VERIFIED", "CLOSED"))
        second = create_episode(db, subscription_id="sub_CYCLE1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        assert second["id"] != first["id"]
        assert second["state"] == "NEW"
        assert second["attempt_count"] == 0  # fresh caps for the new halt


class TestLegalPath:
    def test_full_path_appends_seven_ledger_rows_and_chain_verifies(self, db):
        ep = create_episode(db, subscription_id="sub_PATH1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        path = ("DIAGNOSED", "SCORED", "GATED", "SENT", "VERIFIED", "CLOSED")
        for state in path:
            ep = transition(db, ep["id"], state)
            assert ep["state"] == state
        assert ep["state"] == "CLOSED"

        rows = list(iter_rows(db))
        assert len(rows) == 7  # creation + one per transition
        assert [r["outcome"] for r in rows] == [
            "EPISODE_CREATED",
            *(f"EPISODE_{s}" for s in path),
        ]
        assert {r["subscription_id"] for r in rows} == {"sub_PATH1"}
        ok, detail = verify_chain(rows)
        assert ok, detail

    def test_sent_transition_stamps_attempt_count_and_action_ts(self, db):
        ep = _make_sent(db, "sub_ATT1")
        assert ep["attempt_count"] == 1
        assert ep["last_action_ts_utc"] is not None
        scored = [
            r for r in iter_rows(db) if r["outcome"] == "EPISODE_SENT"
        ]
        (sent_row,) = scored
        assert sent_row["trigger_event"] == "episode.transition"

    def test_gated_transition_marks_human_gate_in_ledger(self, db):
        _make(db, "sub_GATE1", states=("DIAGNOSED", "SCORED", "GATED"))
        (gated_row,) = [r for r in iter_rows(db) if r["outcome"] == "EPISODE_GATED"]
        assert gated_row["human_gate"] is True
        (diag_row,) = [r for r in iter_rows(db) if r["outcome"] == "EPISODE_DIAGNOSED"]
        assert diag_row["human_gate"] is False


class TestIllegalTransitions:
    def test_illegal_transition_raises_and_writes_nothing(self, db):
        ep = _make(db, "sub_ILL1", states=("DIAGNOSED",))
        db.commit()
        before = get_episode(db, ep["id"])
        n_ledger = len(list(iter_rows(db)))

        with pytest.raises(TransitionError):
            transition(db, ep["id"], "CLOSED")  # DIAGNOSED -> CLOSED: illegal
        db.rollback()

        assert get_episode(db, ep["id"]) == before  # row byte-identical
        assert len(list(iter_rows(db))) == n_ledger  # no new ledger row

    def test_caller_abort_after_legal_transition_rolls_back_both_writes(self, db):
        """State change and ledger row share one transaction: an abort after
        transition() persists neither — evidence can never lag the state."""
        ep = create_episode(db, subscription_id="sub_ATOM1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        db.commit()

        with pytest.raises(RuntimeError), get_conn() as conn:
            transition(conn, ep["id"], "DIAGNOSED")
            raise RuntimeError("caller aborts mid-transaction")

        with get_conn() as conn:
            assert get_episode(conn, ep["id"])["state"] == "NEW"
            # Only the pre-abort creation row survived; the transition and
            # its ledger row rolled back together.
            assert [r["outcome"] for r in iter_rows(conn)] == ["EPISODE_CREATED"]

    def test_void_reason_on_non_void_transition_raises(self, db):
        ep = create_episode(db, subscription_id="sub_VR1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        with pytest.raises(TransitionError):
            transition(db, ep["id"], "DIAGNOSED", void_reason="charged")

    def test_unknown_episode_raises(self, db):
        with pytest.raises(EpisodeNotFoundError):
            transition(db, "ep_missing", "DIAGNOSED")

    def test_terminal_states_accept_no_transitions(self, db):
        ep = _make_sent(db, "sub_TERM1")
        void_open_episodes(db, "sub_TERM1", "charged")
        assert get_episode(db, ep["id"])["state"] == "VOIDED"
        for state in EPISODE_STATES:
            with pytest.raises(TransitionError):
                transition(db, ep["id"], state)


class TestStopEvents:
    def test_charged_voids_open_sent_episode_with_ledger_row(self, db):
        ep = _make_sent(db, "sub_CHG1")

        voided = void_open_episodes(db, "sub_CHG1", "charged")

        assert len(voided) == 1
        assert voided[0]["state"] == "VOIDED"
        assert voided[0]["void_reason"] == "charged"

        rows = list(iter_rows(db))
        assert len(rows) == 6  # creation + 4 legal transitions + 1 void
        last = rows[-1]
        assert last["trigger_event"] == "subscription.charged"
        assert last["outcome"] == "EPISODE_VOIDED"
        assert last["policy_eval"] == {
            "decision": "void",
            "reason": "stop_on_charged",
            "from_state": "SENT",
            "to_state": "VOIDED",
        }
        ok, detail = verify_chain(rows)
        assert ok, detail

        # Terminal: no further outreach from a paid customer, ever.
        with pytest.raises(TransitionError):
            transition(db, ep["id"], "VERIFIED")

    def test_charged_replay_is_idempotent(self, db):
        _make_sent(db, "sub_REPLAY1")
        assert void_open_episodes(db, "sub_REPLAY1", "charged")
        assert void_open_episodes(db, "sub_REPLAY1", "charged") == []
        assert len(list(iter_rows(db))) == 6  # no second void row

    def test_charged_voids_fresh_new_episode_too(self, db):
        ep = create_episode(db, subscription_id="sub_CHGNEW1", halt_ts_utc=HALT_TS, cohort="TREATMENT")
        voided = void_open_episodes(db, "sub_CHGNEW1", "charged")
        assert voided[0]["id"] == ep["id"]
        assert voided[0]["state"] == "VOIDED"
        assert voided[0]["void_reason"] == "charged"
        ok, detail = verify_chain(list(iter_rows(db)))
        assert ok, detail

    def test_cancelled_voids_with_cancelled_reason(self, db):
        _make_sent(db, "sub_CAN1")
        voided = void_open_episodes(db, "sub_CAN1", "cancelled")
        assert voided[0]["void_reason"] == "cancelled"
        (last,) = list(iter_rows(db))[-1:]
        assert last["trigger_event"] == "subscription.cancelled"
        assert last["policy_eval"]["reason"] == "stop_on_cancelled"
        ok, _ = verify_chain(list(iter_rows(db)))
        assert ok

    def test_void_touches_only_open_episodes_of_that_subscription(self, db):
        _make(db, "sub_DONE1", states=("DIAGNOSED", "SCORED", "GATED", "SENT", "VERIFIED", "CLOSED"))
        other = _make_sent(db, "sub_LIVE1")

        assert void_open_episodes(db, "sub_DONE1", "charged") == []
        assert get_episode(db, other["id"])["state"] == "SENT"

    def test_unknown_void_reason_raises(self, db):
        with pytest.raises(ValueError):
            void_open_episodes(db, "sub_X1", "exploded")


class TestIsolation:
    def test_separate_data_dirs_are_independent(self, monkeypatch, tmp_path):
        s = get_settings()
        for name in ("dir_a", "dir_b"):
            monkeypatch.setattr(s, "data_dir", tmp_path / name)
            with get_conn() as conn:
                init_db(conn)
                ep = create_episode(
                    conn, subscription_id=f"sub_{name}", halt_ts_utc=HALT_TS, cohort="TREATMENT"
                )
                assert get_open_episodes(conn, f"sub_{name}")[0]["id"] == ep["id"]
                assert [r["outcome"] for r in iter_rows(conn)] == ["EPISODE_CREATED"]
