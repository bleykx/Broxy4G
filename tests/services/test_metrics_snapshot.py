from uuid import uuid4

from broxy4g.services.metrics_snapshot import MetricsSnapshot, SummaryMetrics


def test_get_returns_none_before_update():
    snapshot = MetricsSnapshot()
    dongle_id = uuid4()
    assert snapshot.get(dongle_id) is None


def test_update_then_get_returns_summary_metrics():
    snapshot = MetricsSnapshot()
    dongle_id = uuid4()
    snapshot.update(dongle_id, "1.2.3.4", 85)
    assert snapshot.get(dongle_id) == SummaryMetrics(public_ip="1.2.3.4", signal_quality=85)


def test_update_overwrites_previous_value():
    snapshot = MetricsSnapshot()
    dongle_id = uuid4()
    snapshot.update(dongle_id, "1.2.3.4", 85)
    snapshot.update(dongle_id, "5.6.7.8", 40)
    assert snapshot.get(dongle_id) == SummaryMetrics(public_ip="5.6.7.8", signal_quality=40)


def test_update_with_none_values():
    snapshot = MetricsSnapshot()
    dongle_id = uuid4()
    snapshot.update(dongle_id, None, None)
    assert snapshot.get(dongle_id) == SummaryMetrics(public_ip=None, signal_quality=None)


def test_remove_clears_entry():
    snapshot = MetricsSnapshot()
    dongle_id = uuid4()
    snapshot.update(dongle_id, "1.2.3.4", 85)
    snapshot.remove(dongle_id)
    assert snapshot.get(dongle_id) is None


def test_remove_unknown_id_is_noop():
    snapshot = MetricsSnapshot()
    snapshot.remove(uuid4())
