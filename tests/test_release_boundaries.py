import hashlib
import struct
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import binary_arch, build_macos, build_windows, package_release
from scripts.prepare_release_environment import check_lock, pins


def test_intel_macos_has_complete_separate_runtime_lock():
    lock_path = check_lock('macos-x86_64')
    locked = pins(lock_path)
    assert locked['numpy'] == '1.26.4'
    assert locked['cryptography'] == '48.0.1'
    for dependency in ('coloredlogs', 'humanfriendly', 'sympy', 'mpmath'):
        assert dependency in locked
    assert '6a43d59c7435339bf8cc99a6e6de5d92584586b6a73175809645d69b17f48e71' in lock_path.read_text()


def test_macos_release_bundle_names_are_architecture_specific():
    assert build_macos.architecture_bundle_name('arm64') == 'DOCXdodyr-arm64.app'
    assert build_macos.architecture_bundle_name('x86_64') == 'DOCXdodyr-x86_64.app'
    with pytest.raises(ValueError):
        build_macos.architecture_bundle_name('universal2')


def test_macos_packaging_rejects_legacy_or_ambiguous_bundle(tmp_path):
    # A legacy name is not enough evidence for a release architecture.
    (tmp_path / 'DOCXdodyr.app').mkdir()
    with pytest.raises((ValueError, FileNotFoundError), match='architecture-specific'):
        package_release._macos_app_path(tmp_path, 'arm64')

    (tmp_path / 'DOCXdodyr-arm64.app').mkdir()
    (tmp_path / 'DOCXdodyr-x86_64.app').mkdir()
    with pytest.raises(ValueError, match='exactly one'):
        package_release._macos_app_path(tmp_path, None)


def test_macos_rejects_mixed_nested_architectures(tmp_path, monkeypatch):
    app = tmp_path / 'Example.app'
    main = app / 'Contents/MacOS/DOCXdodyr'
    main.parent.mkdir(parents=True)
    main.write_bytes(b'\xcf\xfa\xed\xfe')
    lib = app / 'library.so'
    lib.write_bytes(b'\xcf\xfa\xed\xfe')
    monkeypatch.setattr(binary_arch.subprocess, 'run', lambda cmd, **kw: SimpleNamespace(stdout='arm64' if cmd[-1] == str(main) else 'x86_64'))
    with pytest.raises(ValueError, match='mismatch'):
        binary_arch.verify_macos_arch(app)


def test_macos_rejects_wrong_runner_before_build(monkeypatch):
    monkeypatch.setattr(build_macos.platform, 'machine', lambda: 'arm64')
    with pytest.raises(ValueError, match='native runner'):
        build_macos.build_app(arch='x86_64')


def test_pe_requires_x64_and_gui(tmp_path):
    p = tmp_path / 'app.exe'
    data = bytearray(256)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 64)
    data[64:70] = b'PE\0\0\x64\x86'
    data[88:90] = b'\x0b\x02'
    data[156:158] = b'\x02\x00'
    p.write_bytes(data)
    assert binary_arch.verify_windows_pe(p, gui=True) == 'x64'
    data[68:70] = b'\x64\xaa'
    p.write_bytes(data)
    with pytest.raises(ValueError):
        binary_arch.verify_windows_pe(p, gui=True)


@pytest.mark.parametrize('contents', ['', 'garbage\n', '0'*64+'  ../outside.zip\n'])
def test_checksums_reject_empty_malformed_and_traversal(tmp_path, contents):
    sums = tmp_path / 'SHA256SUMS.txt'
    sums.write_text(contents)
    assert not package_release.verify_sha256sums(sums, tmp_path)['valid']


def test_checksums_require_all_artifacts_and_no_duplicates(tmp_path):
    a = tmp_path / 'a.zip'; a.write_bytes(b'a')
    sums = tmp_path / 'SHA256SUMS.txt'
    line = hashlib.sha256(b'a').hexdigest()+'  a.zip\n'
    sums.write_text(line)
    assert package_release.verify_sha256sums(sums, tmp_path)['valid']
    sums.write_text(line*2)
    assert not package_release.verify_sha256sums(sums, tmp_path)['valid']
    sums.write_text(line)
    (tmp_path/'b.zip').write_bytes(b'b')
    assert not package_release.verify_sha256sums(sums, tmp_path)['valid']


@pytest.mark.parametrize('module,flag', [(build_macos, '--password'), (build_windows, '/p')])
def test_signing_password_never_logged(module, flag, monkeypatch, capsys):
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stdout='', stderr='failed'))
    with pytest.raises(RuntimeError) as error:
        module.run_command(['sign', flag, 'synthetic-secret'])
    assert 'synthetic-secret' not in capsys.readouterr().out + str(error.value)


def test_windows_signing_failure_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(build_windows, 'find_signtool', lambda: Path('signtool'))
    monkeypatch.setattr(build_windows.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stdout='', stderr='failure'))
    with pytest.raises(RuntimeError):
        build_windows.sign_binaries(tmp_path, cert_path='synthetic.pfx')


def test_cli_reports_missing_resources_as_failure(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main.sys, 'argv', ['DOCXdodyr', '--self-test'])
    monkeypatch.setattr(main.app_paths, 'get_web_dir', lambda: tmp_path)
    with pytest.raises(SystemExit) as error:
        main.handle_cli_arguments()
    assert error.value.code == 1


def test_cli_report_file_works_without_console(tmp_path, monkeypatch):
    import main
    report = tmp_path/'report.txt'
    monkeypatch.setattr(main.sys, 'argv', ['DOCXdodyr', '--version', '--report-file', str(report)])
    assert main.handle_cli_arguments()
    assert package_release.get_canonical_version() in report.read_text()


def test_model_redirect_rejects_untrusted_and_non_tls():
    from qwen_offline import _ModelRedirectHandler, QwenInstallError
    handler = _ModelRedirectHandler()
    for url in ('http://huggingface.co/model', 'https://huggingface.co.evil.example/model', 'https://user@example.invalid/model', 'file:///tmp/model'):
        with pytest.raises(QwenInstallError):
            handler.redirect_request(None, None, 302, '', {}, url)


def test_model_rejects_duplicate_zip_members(tmp_path):
    import zipfile
    from qwen_offline import import_bundle, QwenInstallError
    path=tmp_path/'model.zip'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('config.json', b'{}')
        z.writestr('config.json', b'{}')
    manifest={'files':[{'name':'config.json','size':2}]}
    with pytest.raises(QwenInstallError, match='повторяющиеся'):
        import_bundle(path,tmp_path/'target',consent=True,manifest=manifest)
    assert not (tmp_path/'target').exists()


def test_model_download_enforces_size_and_cleans_partial(tmp_path, monkeypatch):
    import io
    import qwen_offline
    response=io.BytesIO(b'oversized')
    monkeypatch.setattr(qwen_offline,'_open_model_url',lambda *a,**k:response)
    path=tmp_path/'weights'
    with pytest.raises(qwen_offline.QwenInstallError,match='размер'):
        qwen_offline._download_file('https://huggingface.co/model',path,expected_size=2,expected_hash=None,timeout=1)
    assert not path.exists()
    assert not path.with_name('weights.part').exists()


def test_packaged_smoke_ignores_caller_legacy_settings(tmp_path, monkeypatch):
    try:
        from ocr_backend import create_ocr_backend
        create_ocr_backend(language="eng")
    except Exception:
        pytest.skip("OCR backend is not installed on this machine")
    import packaged_smoke
    caller = tmp_path / 'caller'
    caller.mkdir()
    settings = caller / 'settings.json'
    content = '{"enabled_placeholders": []}'
    settings.write_text(content)
    monkeypatch.chdir(caller)
    reads=[]
    original=Path.read_text
    def record_read(path, *args, **kwargs):
        if path.resolve()==settings:reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', record_read)
    assert packaged_smoke.run()['status']=='ok'
    assert Path.cwd()==caller
    assert not reads
    assert original(settings)==content
