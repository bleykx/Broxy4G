"""Tests de la sélection déterministe d'un membre de pool (aucune I/O)."""

from uuid import UUID, uuid4

from broxy4g.domain.pool_selection import PoolCandidate, pick_candidate


def _candidate(proxy_id: UUID | None = None) -> PoolCandidate:
    return PoolCandidate(
        proxy_id=proxy_id or uuid4(),
        host="172.28.0.5",
        port=3128,
        username="alice",
        password="secret",
    )


def test_pick_candidate_empty_list_is_none():
    assert pick_candidate([]) is None


def test_pick_candidate_fewest_in_flight_wins():
    a, b = _candidate(), _candidate()
    in_flight = {a.proxy_id: 5, b.proxy_id: 1}
    assert pick_candidate([a, b], in_flight=in_flight) is b


def test_pick_candidate_tie_on_in_flight_uses_oldest_last_used():
    a, b = _candidate(), _candidate()
    in_flight = {a.proxy_id: 2, b.proxy_id: 2}
    last_used = {a.proxy_id: 100.0, b.proxy_id: 10.0}
    assert pick_candidate([a, b], in_flight=in_flight, last_used=last_used) is b


def test_pick_candidate_full_tie_uses_smallest_proxy_id_string():
    a, b = _candidate(), _candidate()
    winner = min(a, b, key=lambda c: str(c.proxy_id))
    assert pick_candidate([a, b]) is winner


def test_pick_candidate_missing_keys_count_as_zero():
    """Les clés absentes des mappings comptent pour 0 / 0.0 : pas d'exception."""
    a, b = _candidate(), _candidate()
    winner = min(a, b, key=lambda c: str(c.proxy_id))
    assert pick_candidate([a, b], in_flight={a.proxy_id: 0}, last_used={b.proxy_id: 0.0}) is winner


def test_pick_candidate_does_not_mutate_mappings():
    a, b = _candidate(), _candidate()
    in_flight = {a.proxy_id: 3}
    last_used = {b.proxy_id: 7.0}
    in_flight_copy = dict(in_flight)
    last_used_copy = dict(last_used)
    pick_candidate([a, b], in_flight=in_flight, last_used=last_used)
    assert in_flight == in_flight_copy
    assert last_used == last_used_copy
