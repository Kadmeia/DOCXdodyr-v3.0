# -*- coding: utf-8 -*-
"""Legal-identifier analyzer cartridge for PullentiPython.

The package deliberately lives next to Pullenti instead of patching generated
SDK files in ``site-packages``.  Pullenti's own documentation warns that those
files are regenerated on every release.  ``initialize()`` registers this
cartridge through the public ``ProcessorService`` extension point, so its
referents are part of the normal Pullenti analysis result.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"
PACKAGE_NAME = "docxdodyr-pullenti-legal"


class PullentiDependencyError(ImportError):
    """Raised when the cartridge is used without PullentiPython installed."""


def _analyzer_module():
    try:
        from . import analyzer
    except ImportError as exc:
        if exc.name and (exc.name == "pullenti" or exc.name.startswith("pullenti.")):
            raise PullentiDependencyError(
                "PullentiPython is required for the legal cartridge. "
                "Install it with `pip install 'PullentiPython>=0.1,<0.2'`."
            ) from exc
        raise
    return analyzer


def initialize() -> None:
    """Register the legal cartridge once."""

    _analyzer_module().LegalEntityAnalyzer.initialize()


def register_with_pullenti() -> None:
    """Explicit registration entry point used by host applications."""

    initialize()


def create_analyzer() -> Any:
    """Return a fresh analyzer instance for a custom Pullenti processor."""

    return _analyzer_module().LegalEntityAnalyzer()


def __getattr__(name: str) -> Any:
    # Keep ``from pullenti_legal import LegalEntityAnalyzer`` compatible while
    # allowing metadata-only inspection in a clean environment without the
    # runtime dependency imported yet.
    if name == "LegalEntityAnalyzer":
        return _analyzer_module().LegalEntityAnalyzer
    if name == "LegalEntityReferent":
        return _analyzer_module().LegalEntityReferent
    raise AttributeError(name)


__all__ = [
    "PACKAGE_NAME",
    "PullentiDependencyError",
    "LegalEntityAnalyzer",
    "LegalEntityReferent",
    "create_analyzer",
    "initialize",
    "register_with_pullenti",
    "__version__",
]
