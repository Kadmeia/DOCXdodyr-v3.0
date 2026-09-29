# Pullenti legal cartridge: packaging and upstream preparation

## Scope and status

The `pullenti_legal/` directory is packaged as `docxdodyr-pullenti-legal`, a
separate extension distribution. It uses PullentiPython's public
`ProcessorService.register_analyzer` API and never patches generated modules
under `site-packages`. Its entry point is:

```text
pullenti.cartridges: legal = pullenti_legal:initialize
```

This is not the Pullenti upstream package and is not an official upstream fork.
No release, upload, or pull request is performed by the local build workflow.

## Reproducible local build

Use a clean virtual environment and install the dependency from the approved
package source before installing the wheel:

```bash
python3.11 -m venv .venv-pullenti-legal
.venv-pullenti-legal/bin/python -m pip install --upgrade pip build
.venv-pullenti-legal/bin/python -m pip install 'PullentiPython>=0.1,<0.2'
.venv-pullenti-legal/bin/python -m build --sdist --wheel
.venv-pullenti-legal/bin/python -m pip install dist/docxdodyr_pullenti_legal-*.whl
.venv-pullenti-legal/bin/python -c 'import pullenti_legal; pullenti_legal.initialize()'
```

For an offline smoke test, build with the repository's existing build tools,
install the wheel with `--no-deps`, and verify metadata/importability. A real
processor smoke test still requires PullentiPython; do not treat an import-only
test as evidence that NER registration works.

## Contract guarantees

- Only CPython 3.11.x is supported by the package metadata and project runtime policy.
- PullentiPython is bounded to the tested `0.1.x` API line.
- `initialize()` is idempotent and registration occurs only through the public
  Pullenti service.
- `LegalEntityReferent` preserves kind/value and source offsets for adapters.
- Existing project integration through `legal_pullenti.initialize_ner()` remains
  unchanged.

## Preparing an upstream contribution

Before proposing changes upstream, prepare a focused branch containing only
the cartridge and its tests, then:

1. Replace project-specific naming and placeholders with the upstream naming
   convention agreed by Pullenti maintainers.
2. Add the exact PullentiPython version and license provenance used by each
   copied API surface; do not copy generated SDK files.
3. Attach contract-test results for every supported Python/Pullenti matrix and
   a corpus report with false-positive/false-negative examples.
4. Document the Russian grammar, checksum rules, source-offset convention and
   registration order in the proposed API.
5. Ask maintainers whether the extension belongs in Pullenti core, a separately
   maintained cartridge repository, or an official plugin namespace.
6. Submit a pull request only after maintainer direction and legal/license
   review. This repository does not publish the package or open that PR.
