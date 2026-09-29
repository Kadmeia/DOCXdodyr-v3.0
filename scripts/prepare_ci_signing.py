"""Explicit protected-CI-only certificate provisioning. Never used by local audit."""
import argparse
import base64
import os
from pathlib import Path
import secrets
import subprocess


def prepare(target, cleanup=False):
    if os.environ.get('GITHUB_ACTIONS') != 'true' or not os.environ.get('RUNNER_TEMP'):
        raise RuntimeError('Signing provisioning is restricted to explicit CI runs')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    certificate = temporary/'docxdodyr-release-certificate.p12'
    keychain = temporary/'docxdodyr-release-signing.keychain-db'
    if cleanup:
        if target.startswith('macos') and keychain.exists():
            subprocess.run(['security', 'delete-keychain', str(keychain)], check=True, capture_output=True)
        certificate.unlink(missing_ok=True)
        return
    if certificate.exists() or keychain.exists():
        raise RuntimeError('Refusing to replace existing signing material')
    encoded = os.environ.get('RELEASE_CERTIFICATE_BASE64')
    password = os.environ.get('RELEASE_CERTIFICATE_PASSWORD')
    if not encoded or not password:
        raise RuntimeError('Protected certificate and password secrets are required')
    with certificate.open('xb') as stream:
        stream.write(base64.b64decode(encoded, validate=True))
    certificate.chmod(0o600)
    if target.startswith('macos'):
        secret = secrets.token_urlsafe(32)
        commands = [
            ['security', 'create-keychain', '-p', secret, str(keychain)],
            ['security', 'set-keychain-settings', '-lut', '21600', str(keychain)],
            ['security', 'unlock-keychain', '-p', secret, str(keychain)],
            ['security', 'import', str(certificate), '-P', password, '-T', '/usr/bin/codesign', '-t', 'cert', '-f', 'pkcs12', '-k', str(keychain)],
            ['security', 'set-key-partition-list', '-S', 'apple-tool:,apple:', '-k', secret, str(keychain)],
            ['security', 'list-keychains', '-d', 'user', '-s', str(keychain)],
        ]
        for command in commands:
            result = subprocess.run(command, capture_output=True)
            if result.returncode:
                # Do not emit arguments/stdout/stderr containing credentials.
                raise RuntimeError('CI keychain provisioning failed')
    else:
        with Path(os.environ['GITHUB_ENV']).open('a', encoding='utf-8') as stream:
            stream.write('WINDOWS_CODESIGN_CERT=' + str(certificate) + '\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--target', choices=('macos-arm64', 'macos-x86_64', 'windows-x64'), required=True)
    p.add_argument('--cleanup', action='store_true')
    a = p.parse_args()
    prepare(a.target, a.cleanup)
