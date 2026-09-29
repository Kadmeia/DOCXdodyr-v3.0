"""Safety rails for the local-only Antigravity suite."""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    for key, value in {
        "DOCXDODYR_ANTIGRAVITY_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }.items():
        os.environ.setdefault(key, value)
    config.addinivalue_line("markers", "antigravity_ui: human-like UI/service scenario")
    config.addinivalue_line("markers", "antigravity_backend: direct backend contract scenario")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail fast if a regression accidentally tries to use the network."""

    def blocked(*_args, **_kwargs):
        raise AssertionError("Antigravity suite is offline; network access is forbidden")

    monkeypatch.setattr("urllib.request.urlopen", blocked)
