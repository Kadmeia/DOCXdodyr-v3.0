import os
from pathlib import Path
import subprocess
import sys
import pytest
import version


@pytest.mark.skipif(
    sys.platform not in ('darwin', 'win32') or os.environ.get('DOCXDODYR_RUN_NATIVE_GUI') != '1',
    reason='Native desktop WebView requires an interactive session and DOCXDODYR_RUN_NATIVE_GUI=1',
)
def test_offline_help_roundtrip_and_diagnostics():
    source = r'''
import os, time
import webview
from main import ApiWrapper
import app_paths
api=ApiWrapper()
window=webview.create_window('Synthetic offline help test',str(app_paths.get_web_dir()/'help.html'),js_api=api,width=900,height=700)
api.set_window(window)
def checks():
    try:
        for _ in range(100):
            text=window.evaluate_js("document.getElementById('runtime').textContent")
            if version.__version__ in str(text): break
            time.sleep(.1)
        assert version.__version__ in text and ('arm64' in text or 'AMD64' in text or 'x86_64' in text), text
        window.evaluate_js("document.getElementById('diagnostic').click()")
        for _ in range(100):
            text=window.evaluate_js("document.getElementById('diagnostic-result').textContent")
            if 'python' in str(text).lower(): break
            time.sleep(.1)
        assert 'python' in text.lower(), text
        window.evaluate_js("window.location.href='index.html'")
        for _ in range(100):
            if window.evaluate_js("!!document.getElementById('dropzone-anonymize')"): break
            time.sleep(.1)
        assert window.evaluate_js("!!document.getElementById('dropzone-anonymize')")
        print('OFFLINE_HELP_PASS',flush=True)
        api.shutdown()
        window.destroy()
    except BaseException as e:
        print(type(e).__name__,flush=True)
        os._exit(1)
webview.start(checks)
'''
    result = subprocess.run([sys.executable, '-c', source], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert result.returncode == 0 and 'OFFLINE_HELP_PASS' in result.stdout, result.stderr + result.stdout
