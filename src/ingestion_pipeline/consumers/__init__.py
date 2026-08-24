"""
Downstream consumers of the ingestion pipeline's event outbox
(core/events.py). Each consumer polls ingestion_events for a topic it cares
about and tracks its own read position in event_consumer_offsets -- it
never touches the core orchestrator or its fixed stage order.
"""
