from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import OperationSpec

OperationHandler = Callable[[OperationSpec], dict[str, Any]]


class OperationRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, OperationHandler] = {}

    def register(self, name: str, handler: OperationHandler) -> None:
        if name in self._handlers:
            raise ValueError(f"Operation handler already registered: {name}")
        self._handlers[name] = handler

    def get(self, name: str) -> OperationHandler:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise KeyError(f"Unknown operation handler: {name}") from exc

    def list_names(self) -> list[str]:
        return sorted(self._handlers)
