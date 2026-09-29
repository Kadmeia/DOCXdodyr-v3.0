"""Packaging/API contracts for the standalone Pullenti legal cartridge."""

import importlib.metadata
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _distribution():
    try:
        distribution = importlib.metadata.distribution("docxdodyr-pullenti-legal")
        # A local ``egg-info`` directory is a build by-product, not an
        # installed distribution, and old setuptools may omit PEP 621 fields
        # from that transient metadata. Validate the source declaration below.
        if Path(str(distribution._path)).parent == ROOT:
            return None
        return distribution
    except importlib.metadata.PackageNotFoundError:
        return None


def test_project_metadata_is_standalone_and_dependency_is_bounded():
    distribution = _distribution()
    if distribution is None:
        # Source checkout contract: the same values are declared in the
        # canonical PEP 621 file before an editable install is made.
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'name = "docxdodyr-pullenti-legal"' in pyproject
        assert 'version = "0.1.0"' in pyproject
        assert 'dependencies = ["PullentiPython>=0.1,<0.2"]' in pyproject
        return
    metadata = distribution.metadata
    assert metadata["Name"] == "docxdodyr-pullenti-legal"
    assert metadata["Version"] == "0.1.0"
    requirements = [value.lower() for value in (metadata.get_all("Requires-Dist") or [])]
    assert any(
        value.startswith("pullentipython")
        and ">=0.1" in value
        and "<0.2" in value
        for value in requirements
    )


def test_public_api_is_lazy_and_exposes_registration_contract():
    import pullenti_legal

    assert pullenti_legal.__version__ == "0.1.0"
    assert pullenti_legal.PACKAGE_NAME == "docxdodyr-pullenti-legal"
    assert callable(pullenti_legal.initialize)
    assert callable(pullenti_legal.register_with_pullenti)
    assert callable(pullenti_legal.create_analyzer)


def test_entry_point_targets_public_initialize_function():
    distribution = _distribution()
    if distribution is None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert '[project.entry-points."pullenti.cartridges"]' in pyproject
        assert 'legal = "pullenti_legal:initialize"' in pyproject
        return
    entry_points = importlib.metadata.entry_points()
    selected = entry_points.select(group="pullenti.cartridges", name="legal")
    assert len(selected) == 1
    assert selected[0].value == "pullenti_legal:initialize"


def test_package_does_not_vendor_or_modify_pullenti_site_packages():
    assert not list((ROOT / "pullenti_legal").glob("pullenti/**"))
    assert not (ROOT / "site-packages").exists()


def test_registration_is_idempotent_with_runtime_dependency():
    pytest.importorskip("pullenti")
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti_legal import create_analyzer, initialize

    initialize()
    initialize()
    assert create_analyzer().name == "LEGALENTITY"
    assert ProcessorService.get_version()
