"""Legacy build fallback for environments with old setuptools.

The canonical metadata lives in ``pyproject.toml``. This shim keeps
``python setup.py sdist bdist_wheel`` available for the Python 3.11 runtime
that ships with the application; modern installers use PEP 621 metadata.
"""

from setuptools import find_packages, setup
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


setup(
    name="docxdodyr-pullenti-legal",
    version="0.1.0",
    description="Context-aware Russian legal identifier cartridge for PullentiPython",
    long_description=(PROJECT_ROOT / "pullenti_legal" / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    packages=find_packages(include=["pullenti_legal", "pullenti_legal.*"]),
    package_data={"pullenti_legal": ["README.md", "LICENSE"]},
    python_requires=">=3.11,<3.12",
    install_requires=["PullentiPython>=0.1,<0.2"],
    entry_points={"pullenti.cartridges": ["legal = pullenti_legal:initialize"]},
    author="DOCXdodyr contributors",
    url="https://github.com/ssharkov03/pullenti",
    license="MIT",
    include_package_data=True,
)
