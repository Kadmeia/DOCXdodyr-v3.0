# DOCXdodyr Pullenti legal cartridge

`docxdodyr-pullenti-legal` is an installable extension for the public
PullentiPython `ProcessorService` API. It adds context-bound Russian legal and
personal identifier referents (`LEGALENTITY`) without editing Pullenti's
generated files or anything in `site-packages`.

This is a DOCXdodyr extension package, not an upstream Pullenti release or an
official Pullenti fork. The package does not claim upstream status. Upstream
contribution preparation is documented in
`docs/PULLENTI_LEGAL_PACKAGING.md` in the source repository.

## Install

```bash
python -m pip install docxdodyr-pullenti-legal
```

The runtime dependency is bounded to `PullentiPython>=0.1,<0.2`. Registration
is explicit and idempotent:

```python
from pullenti_legal import initialize
from pullenti.ner.ProcessorService import ProcessorService

ProcessorService.initialize()
initialize()
processor = ProcessorService.create_processor()
```

The same function is exposed as the `pullenti.cartridges` entry point named
`legal` for hosts that support plugin discovery. Existing DOCXdodyr imports
(`from pullenti_legal import initialize`) remain valid.

## Development

```bash
python -m pip install -e '.[test]'
python -m pytest tests/test_pullenti_package_contract.py tests/test_pullenti_legal_cartridge.py
python -m build --sdist --wheel
```

Build artifacts are intentionally not published by this repository change.
