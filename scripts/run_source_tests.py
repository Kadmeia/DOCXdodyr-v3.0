"""Native source suite. Packaged tests run separately after build; Qwen is optional."""
from pathlib import Path
import subprocess
import os
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.check_test_results import check

PACKAGED = [
 'tests/test_stage06_macos_app_and_dmg.py::test_app_bundle_structure_and_signature',
 'tests/test_stage06_macos_app_and_dmg.py::test_dmg_verification',
 *['tests/test_stage09_installed_app_verification.py::'+name for name in (
 'test_stage09_exe_binary_presence_and_pe_structure','test_stage09_exe_cli_version_and_flags','test_stage09_desktop_exe_process_and_task_manager','test_stage09_desktop_ui_e2e_clicks_and_folder_processing')]]
MAC_ONLY = ['tests/test_frontend_e2e_interactions.py::test_e2e_frontend_dom_and_user_interactions', 'tests/test_window_lifecycle_and_stress.py::test_real_window_close_on_cross_button_subprocess', 'tests/test_window_lifecycle_and_stress.py::test_stress_window_open_and_close_cycles']

WIN_SYMLINK = [
    'tests/test_folder_pipeline.py::test_broken_provenance_symlink_never_uses_legacy_fallback',
    'tests/test_production_gate.py::test_production_rejects_symlink_artifact',
]

if __name__=='__main__':
    if sys.platform not in ('darwin','win32'):
        raise SystemExit('Release source suite requires a native desktop runner')
    output=Path(sys.argv[1] if len(sys.argv)>1 else 'source-tests.xml').resolve()
    args=[sys.executable,'-m','pytest','-q',f'--junitxml={output}','--ignore=tests/test_qwen_real_smoke.py']
    for node in PACKAGED + (MAC_ONLY if sys.platform!='darwin' else []) + (WIN_SYMLINK if sys.platform=='win32' else []):
        args+=['--deselect',node]
    if not os.environ.get('QWEN_MODEL_DIR'):
        args += ['--deselect', 'tests/test_release_review_2026_09_15_regressions.py::test_repro_qwen_positive_grammar_and_sentinel_preservation']
    print('Base-source scope: packaged and real-Qwen acceptance are separate mandatory native evidence; see exclusion list in this script.')
    subprocess.run(args,check=True,cwd=Path(__file__).resolve().parent.parent)
    print(f'{check(output)} required source tests passed without skips')
