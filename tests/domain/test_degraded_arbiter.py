"""Règles d'entrée/sortie de DEGRADED (hystérésis asymétrique)."""

import pytest

from broxy4g.domain.degraded_arbiter import ArbiterDecision, DegradedArbiter, ProbeVerdict


def test_ip_ok_restores_online_and_resets_counter():
    arbiter = DegradedArbiter()
    decision, count = arbiter.decide(ProbeVerdict.OK, ProbeVerdict.FAILED, weak_count=3)
    assert decision is ArbiterDecision.RESTORE_ONLINE
    assert count == 0


def test_ip_failed_and_ping_failed_marks_degraded_immediately():
    """Blackhole concordant : MARK_DEGRADED au 1er cycle, sans hystérésis."""
    arbiter = DegradedArbiter(min_consecutive=5)
    decision, _ = arbiter.decide(ProbeVerdict.FAILED, ProbeVerdict.FAILED, weak_count=0)
    assert decision is ArbiterDecision.MARK_DEGRADED


def test_ip_failed_with_ping_ok_keeps_state_on_first_cycle():
    arbiter = DegradedArbiter()  # min_consecutive = 2
    decision, count = arbiter.decide(ProbeVerdict.FAILED, ProbeVerdict.OK, weak_count=0)
    assert decision is ArbiterDecision.KEEP_STATE
    assert count == 1


def test_ip_failed_with_ping_ok_marks_degraded_on_second_cycle():
    arbiter = DegradedArbiter()
    decision, _ = arbiter.decide(ProbeVerdict.FAILED, ProbeVerdict.OK, weak_count=1)
    assert decision is ArbiterDecision.MARK_DEGRADED


def test_not_run_alone_does_not_mark_degraded_on_first_cycle():
    arbiter = DegradedArbiter()
    decision, count = arbiter.decide(ProbeVerdict.NOT_RUN, ProbeVerdict.NOT_RUN, weak_count=0)
    assert decision is ArbiterDecision.KEEP_STATE
    assert count == 1


def test_counter_reset_after_restore_online():
    arbiter = DegradedArbiter()
    # Deux échecs faibles consécutifs...
    d1, c1 = arbiter.decide(ProbeVerdict.FAILED, ProbeVerdict.OK, weak_count=0)
    d2, c2 = arbiter.decide(ProbeVerdict.FAILED, ProbeVerdict.OK, weak_count=c1)
    assert (d1, d2) == (ArbiterDecision.KEEP_STATE, ArbiterDecision.MARK_DEGRADED)
    # ...puis l'IP revient : compteur remis à 0 (sortie immédiate).
    d3, c3 = arbiter.decide(ProbeVerdict.OK, ProbeVerdict.OK, weak_count=c2)
    assert d3 is ArbiterDecision.RESTORE_ONLINE
    assert c3 == 0
    # L'hystérésis repart de zéro : un échec faible seul ne suffit plus.
    d4, _ = arbiter.decide(ProbeVerdict.FAILED, ProbeVerdict.OK, weak_count=c3)
    assert d4 is ArbiterDecision.KEEP_STATE


def test_min_consecutive_configurable():
    arbiter = DegradedArbiter(min_consecutive=3)
    d1, c1 = arbiter.decide(ProbeVerdict.NOT_RUN, ProbeVerdict.OK, 0)
    d2, c2 = arbiter.decide(ProbeVerdict.NOT_RUN, ProbeVerdict.OK, c1)
    d3, _ = arbiter.decide(ProbeVerdict.NOT_RUN, ProbeVerdict.OK, c2)
    assert (d1, d2) == (ArbiterDecision.KEEP_STATE, ArbiterDecision.KEEP_STATE)
    assert d3 is ArbiterDecision.MARK_DEGRADED


def test_min_consecutive_must_be_positive():
    with pytest.raises(ValueError):
        DegradedArbiter(min_consecutive=0)
