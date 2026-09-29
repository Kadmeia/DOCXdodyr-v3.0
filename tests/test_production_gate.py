import json
from scripts import release_gate, package_release
import pytest


@pytest.fixture
def release_dist(tmp_path):
    # Keep the candidate inventory separate from autouse user-data directories.
    dist = tmp_path / 'candidate'
    dist.mkdir()
    return dist


def evidence_set(release_dist):
    commit='1'*40
    for target in release_gate.TARGETS:
        names = sorted(release_gate.artifact_names(target))
        hashes={}
        for name in names:
            p=release_dist/name;p.write_bytes(b'synthetic package')
            hashes[name]=package_release.compute_sha256(p)
        checks={key:'pass' for key in release_gate.CHECKS+('notarization','stapling','macos14','no_rosetta','windows10','windows11','webview2')}
        for name in (release_gate.metadata_names(target) | release_gate.COMMON_FILES) - {f'{target}-build-provenance.json'}:
            value = '<testsuite><testcase name="synthetic"/></testsuite>' if name.endswith('.xml') else json.dumps({
                'bomFormat': 'CycloneDX', 'components': [{'name':'synthetic'}],
                'metadata': {'component': {'version': release_gate.__version__}}})
            (release_dist/name).write_text(value)
            hashes[name] = package_release.compute_sha256(release_dist/name)
        provenance_name = f'{target}-build-provenance.json'
        (release_dist/provenance_name).write_text(json.dumps({'schema':'docxdodyr.build/v1', 'channel':'signed-stable',
            'commit':commit,'target':target,'version':release_gate.__version__,'sha256':dict(hashes)}))
        hashes[provenance_name] = package_release.compute_sha256(release_dist/provenance_name)
        (release_dist/f'{target}-production-evidence.json').write_text(json.dumps({'commit':commit,'target':target,'version':release_gate.__version__,'checks':checks,'sha256':hashes}))
    package_release.generate_sha256sums(release_dist)
    return commit


def test_production_rejects_missing_platform_evidence(release_dist):
    commit=evidence_set(release_dist)
    (release_dist/'macos-x86_64-production-evidence.json').unlink()
    with pytest.raises(ValueError, match='checksums'):release_gate.verify(release_dist,commit)


def test_production_rejects_changed_artifact_even_if_checksums_regenerated(release_dist):
    commit=evidence_set(release_dist)
    next(release_dist.glob('*.dmg')).write_bytes(b'changed after validation')
    package_release.generate_sha256sums(release_dist)
    with pytest.raises(ValueError,match='evidence'):release_gate.verify(release_dist,commit)


def test_production_rejects_other_commit(release_dist):
    evidence_set(release_dist)
    with pytest.raises(ValueError,match='different build'):release_gate.verify(release_dist,'2'*40)


def test_production_rejects_skipped_clean_machine_check(release_dist):
    commit=evidence_set(release_dist)
    path=release_dist/'windows-x64-production-evidence.json';data=json.loads(path.read_text());data['checks']['clean_install']='skip';path.write_text(json.dumps(data))
    package_release.generate_sha256sums(release_dist)
    with pytest.raises(ValueError,match='Missing production'):release_gate.verify(release_dist,commit)


def test_production_rejects_wrong_version(release_dist):
    commit=evidence_set(release_dist)
    path=release_dist/'macos-arm64-production-evidence.json'
    data=json.loads(path.read_text())
    data['version']='2.2.0'
    path.write_text(json.dumps(data))
    package_release.generate_sha256sums(release_dist)
    with pytest.raises(ValueError,match='different build'):release_gate.verify(release_dist,commit)


def test_production_rejects_missing_notarization_or_macos14(release_dist):
    commit=evidence_set(release_dist)
    path=release_dist/'macos-arm64-production-evidence.json'
    data=json.loads(path.read_text())
    data['checks']['macos14']='fail'
    path.write_text(json.dumps(data))
    package_release.generate_sha256sums(release_dist)
    with pytest.raises(ValueError,match='Missing production evidence'):release_gate.verify(release_dist,commit)


def test_production_rejects_symlink_artifact(release_dist):
    commit=evidence_set(release_dist)
    dmg = next(release_dist.glob('*.dmg'))
    real_target = release_dist / 'real.dmg'
    dmg.rename(real_target)
    try:
        dmg.symlink_to(real_target)
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")
    with pytest.raises(ValueError):release_gate.verify(release_dist,commit)


def test_production_rejects_extra_unexpected_artifact(release_dist):
    commit=evidence_set(release_dist)
    extra = release_dist / 'unexpected.dmg'
    extra.write_bytes(b'rogue')
    package_release.generate_sha256sums(release_dist)
    with pytest.raises(ValueError,match='Unexpected or missing'):release_gate.verify(release_dist,commit)


def test_complete_synthetic_contract_is_accepted(release_dist):
    assert release_gate.verify(release_dist, evidence_set(release_dist)) is True


@pytest.mark.parametrize('kind', ['hidden-file', 'directory', 'dangling-symlink', 'checksum-symlink'])
def test_production_rejects_entries_omitted_from_checksum_inventory(release_dist, kind):
    dist = release_dist / 'dist'
    dist.mkdir()
    commit = evidence_set(dist)
    if kind == 'hidden-file':
        (dist / '.unlisted').write_text('synthetic')
    elif kind == 'directory':
        (dist / 'unlisted').mkdir()
        (dist / 'unlisted' / 'payload').write_text('synthetic')
    elif kind == 'dangling-symlink':
        try:
            (dist / 'unlisted').symlink_to(release_dist / 'absent')
        except OSError:
            pytest.skip("Symlink creation requires administrative privileges on Windows")
    else:
        sums = dist / 'SHA256SUMS.txt'
        external = release_dist / 'external-checksums.txt'
        sums.rename(external)
        try:
            sums.symlink_to(external)
        except OSError:
            pytest.skip("Symlink creation requires administrative privileges on Windows")
    with pytest.raises(ValueError, match='inventory|Unexpected'):
        release_gate.verify(dist, commit)


@pytest.mark.parametrize('suffix', ['environment-sbom.json', 'build-provenance.json', 'source-tests.xml'])
def test_metadata_tamper_fails_even_with_new_checksums(release_dist, suffix):
    commit = evidence_set(release_dist)
    path = release_dist / ('macos-arm64-' + suffix)
    path.write_text(path.read_text() + ' ')
    package_release.generate_sha256sums(release_dist)
    with pytest.raises(ValueError, match='evidence'):
        release_gate.verify(release_dist, commit)
