"""Verify source-archive exclusions using a synthetic Git repository only."""
import io
from pathlib import Path
import subprocess
import tarfile


def test_private_material_excluded_but_secret_defenses_preserved(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    policy = Path(__file__).resolve().parents[1] / '.gitattributes'
    (repo / '.gitattributes').write_bytes(policy.read_bytes())
    private = ['1/summary.md', '.antigravity/handoffs/example.md',
               'scratch/old_experiment.py', 'artifacts/report.json',
               'tests/source_files/example.docx', 'tests/test_data/example.txt',
               'tests/run_auto_tests.py', 'DOCXDODYR_ANTIGRAVITY_38_FLASH_HANDOFF.md']
    public = ['main.py', 'app/log_sanitizer.py', 'scripts/scan_release_sources.py',
              'tests/test_release_source_scan.py', 'tests/test_release_privacy.py']
    for name in private + public:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic fixture\n')
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo, stderr=subprocess.DEVNULL)
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Synthetic', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'Synthetic archive fixture')
    with tarfile.open(fileobj=io.BytesIO(git('archive', '--format=tar', 'HEAD'))) as archive:
        names = set(archive.getnames())
    assert not names.intersection(private)
    assert set(public).issubset(names)
    # Original repository objects remain available: this is NOT history purge.
    assert git('show', 'HEAD:' + private[0]) == b'synthetic fixture\n'
