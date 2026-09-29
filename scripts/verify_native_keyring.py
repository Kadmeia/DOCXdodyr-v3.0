"""Explicit native integration check using one disposable, unique OS key entry."""
import json
from pathlib import Path
import sys
import tempfile
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from review_context import SecureContextVault


def verify():
    import keyring
    service = 'DOCXdodyr.synthetic-release-test.' + uuid.uuid4().hex
    account = 'synthetic-only'
    try:
        with tempfile.TemporaryDirectory(prefix='docxdodyr-keyring-') as directory:
            path = Path(directory) / 'vault.json'
            context = {'synthetic': {'value': 'SYNTHETIC-VAULT-MARKER'}}
            SecureContextVault(path, service=service, account=account).save(context)
            assert b'SYNTHETIC-VAULT-MARKER' not in path.read_bytes()
            assert SecureContextVault(path, service=service, account=account).load() == context
    finally:
        # Only this invocation's unique entry can be touched.
        if keyring.get_password(service, account) is not None:
            keyring.delete_password(service, account)
    assert keyring.get_password(service, account) is None
    return {'native_keyring_vault_roundtrip': 'pass', 'synthetic_entry_removed': True,
            'backend': type(keyring.get_keyring()).__module__, 'packaged_evidence': False}


if __name__ == '__main__':
    print(json.dumps(verify()))
