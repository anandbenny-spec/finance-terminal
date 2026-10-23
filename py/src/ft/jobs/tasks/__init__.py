"""Task implementations: one module per task in this package, each class registered under its config/tasks.yaml
key with @register (the slice adds them: B3 policy.calendar, B4 policy.market_series, B5 policy.fed_documents,
B6 policy.rbi_documents, B7 policy.ai_batch). A key in tasks.yaml with no class here isn't built yet: the run CLI
names it and runs the rest."""
from __future__ import annotations

import importlib
import pkgutil

from ft.jobs.runner import Task

_REGISTRY: dict[str, type[Task]] = {}


def register(cls: type[Task]) -> type[Task]:
    if cls.key in _REGISTRY and _REGISTRY[cls.key] is not cls:
        raise ValueError(f"two classes registered for {cls.key}")
    _REGISTRY[cls.key] = cls
    return cls


def registry() -> dict[str, type[Task]]:
    """Every registered task class by key (imports this package's modules first)."""
    for mod in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{mod.name}")
    return dict(_REGISTRY)
