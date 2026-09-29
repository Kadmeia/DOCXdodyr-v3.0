"""Behavioural negatives: no native process or user data is used here."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
import psutil
from scripts import verify_packaged_app as verifier

class Process:
    pid = 42
    def __init__(self, stuck=False):
        self.returncode = None
        self.stuck = stuck
    def poll(self):
        return self.returncode
    def kill(self):
        if not self.stuck:
            self.returncode = -9
    def wait(self, timeout):
        if self.stuck:
            raise subprocess.TimeoutExpired('synthetic', timeout)
        return self.returncode

def missing_process(_):
    raise psutil.NoSuchProcess(42)

def test_cleanup_handles_already_missing_process(monkeypatch):
    monkeypatch.setattr(psutil, 'Process', missing_process)
    process = Process()
    verifier._terminate_process_tree(process, psutil)
    assert process.poll() == -9

def test_actual_subprocess_timeout_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(psutil, 'Process', missing_process)
    with pytest.raises(RuntimeError, match='exit could not be verified'):
        verifier._terminate_process_tree(Process(stuck=True), psutil)

@pytest.mark.parametrize('gui', [False, True])
@pytest.mark.parametrize('healthy', [False, True])
def test_flags_do_not_fabricate_native_evidence(tmp_path, monkeypatch, gui, healthy):
    monkeypatch.setattr(verifier.sys, 'platform', 'darwin')
    monkeypatch.setattr(verifier, 'verify_macos_arch', lambda root: 'arm64')
    monkeypatch.setattr(verifier.platform, 'machine', lambda: 'arm64')
    def run(args, **kwargs):
        payload = {'status': 'ok', 'paths': {'web_index_present': True},
                   'docx_anonymization': True, 'decoder': True, 'docx_restoration': True}
        Path(args[-1]).write_text(verifier.__version__ if args[1] == '--version' else json.dumps(payload))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(verifier.subprocess, 'run', run)
    launched = []
    def launch(args, *, env, cwd):
        assert Path(cwd).is_dir()
        assert all(Path(env['DOCXDODYR_' + key + '_DIR']).parent == Path(cwd)
                   for key in ('DATA', 'USER_DATA', 'CONFIG', 'CACHE', 'LOG'))
        process = Process()
        launched.append(process)
        return process
    monkeypatch.setattr(verifier.subprocess, 'Popen', launch)
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(
        is_running=lambda: True, memory_info=lambda: SimpleNamespace(rss=(30 if healthy else 1)*1024*1024),
        children=lambda recursive: []))
    import time
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    if gui and not healthy:
        with pytest.raises(RuntimeError, match='launch smoke failed'):
            verifier.verify(tmp_path, check_gui=gui, clean_machine=True)
        assert launched[0].poll() == -9
        return
    report = verifier.verify(tmp_path, check_gui=gui, clean_machine=True)
    assert report['clean_machine'] == report['gui'] == 'not-tested'
    assert report['clean_machine_requested'] is True
    assert report['gui_process_smoke'] == ('pass' if gui else 'not-tested')
    assert bool(launched) == gui

def test_empty_smoke_payload_cannot_claim_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.sys, 'platform', 'darwin')
    monkeypatch.setattr(verifier, 'verify_macos_arch', lambda root: 'arm64')
    monkeypatch.setattr(verifier.platform, 'machine', lambda: 'arm64')
    def run(args, **kwargs):
        payload = {'status': 'ok', 'paths': {'web_index_present': True}}
        Path(args[-1]).write_text(verifier.__version__ if args[1] == '--version' else json.dumps(payload))
    monkeypatch.setattr(verifier.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='roundtrip'):
        verifier.verify(tmp_path)
