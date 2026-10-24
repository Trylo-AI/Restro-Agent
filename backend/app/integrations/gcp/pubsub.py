class PubSubSignal:
    """Publishes {cafe_id, conversation_id} to topic wa-inbound (push to /internal/pubsub/inbound). Phase 3."""

    async def publish(self, cafe_id: str, conversation_id: str) -> None:
        raise NotImplementedError("SIGNAL_BACKEND=pubsub arrives in Phase 3; use SIGNAL_BACKEND=local")
