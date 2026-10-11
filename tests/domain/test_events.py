from uuid import uuid4

from broxy4g.domain.events import DomainEvent, EventType


def test_domain_event():
    # Test création DomainEvent
    event = DomainEvent(event_type=EventType.DONGLE_ENROLLED, dongle_id=uuid4())
    assert event.event_type == EventType.DONGLE_ENROLLED
    assert event.dongle_id is not None
    assert event.id is not None
    assert event.timestamp is not None

if __name__ == "__main__":
    test_domain_event()
    print("All tests passed!")