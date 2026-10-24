"""Signal (Pub/Sub wake-up) and task queue (Cloud Tasks) behind one interface.

Locally both are in-process shims that call the worker over HTTP after the delay (decision: local shims are
in memory; a restart drops pending timers, inbound work stays in pending_work). Phase 3 swaps in
integrations/gcp with no caller changes.
"""

from app.core.config import get_settings
from app.integrations.tasks.base import SignalPublisher, TaskQueue
from app.integrations.tasks.local import LocalSignal, LocalTaskQueue

_signal: SignalPublisher | None = None
_tasks: TaskQueue | None = None


def signal() -> SignalPublisher:
    global _signal
    if _signal is None:
        if get_settings().signal_backend != "local":
            from app.integrations.gcp.pubsub import PubSubSignal

            _signal = PubSubSignal()
        else:
            _signal = LocalSignal()
    return _signal


def tasks() -> TaskQueue:
    global _tasks
    if _tasks is None:
        if get_settings().tasks_backend != "local":
            from app.integrations.gcp.cloud_tasks import CloudTaskQueue

            _tasks = CloudTaskQueue()
        else:
            _tasks = LocalTaskQueue()
    return _tasks


def set_backends(signal_impl: SignalPublisher | None = None, tasks_impl: TaskQueue | None = None) -> None:
    """Tests swap in recorders."""
    global _signal, _tasks
    _signal, _tasks = signal_impl, tasks_impl
