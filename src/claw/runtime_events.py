"""Ordered, in-process lifecycle events for the Agent runtime.

The bus intentionally accepts only Python callables registered by trusted runtime
code.  Workspace plugins are adapted separately through a small declarative
surface; loading arbitrary plugin code is outside this module's trust boundary.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Union


@dataclass
class RuntimeEvent:
    """A mutable event passed to ordered listeners."""

    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    phase_id: str = "runtime"
    timestamp: float = field(default_factory=time.time)


@dataclass
class EventDirective:
    """A listener's requested changes to the current lifecycle operation."""

    cancel: bool = False
    reason: str = ""
    payload_updates: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(
        cls,
        value: Optional[Union["EventDirective", Mapping[str, Any]]],
    ) -> "EventDirective":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            updates = value.get("payload_updates", {})
            return cls(
                cancel=bool(value.get("cancel", value.get("deny", False))),
                reason=str(value.get("reason", "")),
                payload_updates=(dict(updates) if isinstance(updates, Mapping) else {}),
            )
        raise TypeError("event listeners must return None, a mapping, or EventDirective")


@dataclass
class EventDispatch:
    """Aggregate result after every applicable listener has run."""

    event: RuntimeEvent
    cancelled: bool = False
    reason: str = ""
    errors: List[str] = field(default_factory=list)


EventListener = Callable[
    [RuntimeEvent], Optional[Union[EventDirective, Mapping[str, Any]]]
]


@dataclass(frozen=True)
class _Subscription:
    token: str
    event_type: str
    callback: EventListener
    priority: int
    sequence: int
    name: str
    fail_closed: bool


class RuntimeEventBus:
    """Deterministic synchronous event dispatcher.

    Lower priority values run first.  Equal-priority listeners retain
    registration order.  A ``*`` subscription observes every event while still
    participating in the same global ordering.
    """

    def __init__(self) -> None:
        self._subscriptions: Dict[str, _Subscription] = {}
        self._sequence = 0

    def on(
        self,
        event_type: str,
        callback: EventListener,
        *,
        priority: int = 0,
        name: Optional[str] = None,
        fail_closed: bool = False,
    ) -> str:
        """Register a listener and return a token suitable for ``off``."""
        normalized = str(event_type).strip()
        if not normalized:
            raise ValueError("event_type must not be empty")
        if not callable(callback):
            raise TypeError("callback must be callable")
        token = uuid.uuid4().hex
        self._subscriptions[token] = _Subscription(
            token=token,
            event_type=normalized,
            callback=callback,
            priority=int(priority),
            sequence=self._sequence,
            name=name or getattr(callback, "__name__", "listener"),
            fail_closed=bool(fail_closed),
        )
        self._sequence += 1
        return token

    def off(self, token: str) -> bool:
        """Remove one listener.  Returns whether a listener was removed."""
        return self._subscriptions.pop(token, None) is not None

    def emit(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        session_id: str = "",
        phase_id: str = "runtime",
    ) -> EventDispatch:
        """Emit an event and merge listener directives in deterministic order."""
        event = RuntimeEvent(
            event_type=str(event_type),
            payload=dict(payload or {}),
            session_id=session_id,
            phase_id=phase_id,
        )
        subscriptions = sorted(
            (
                subscription
                for subscription in self._subscriptions.values()
                if subscription.event_type in {event.event_type, "*"}
            ),
            key=lambda item: (item.priority, item.sequence),
        )
        dispatch = EventDispatch(event=event)
        for subscription in subscriptions:
            try:
                directive = EventDirective.from_value(subscription.callback(event))
            except Exception as exc:  # Extensions must not crash the Agent by default.
                dispatch.errors.append(
                    f"{subscription.name}: {type(exc).__name__}: {exc}"
                )
                if subscription.fail_closed:
                    dispatch.cancelled = True
                    if not dispatch.reason:
                        dispatch.reason = (
                            f"event listener {subscription.name!r} failed closed"
                        )
                continue
            if directive.payload_updates:
                event.payload.update(directive.payload_updates)
            if directive.cancel:
                dispatch.cancelled = True
                if directive.reason and not dispatch.reason:
                    dispatch.reason = directive.reason
        return dispatch

    def listener_count(self, event_type: Optional[str] = None) -> int:
        """Return the number of all listeners or listeners for one event."""
        if event_type is None:
            return len(self._subscriptions)
        return sum(
            subscription.event_type == event_type
            for subscription in self._subscriptions.values()
        )
