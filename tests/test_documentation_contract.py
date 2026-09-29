from __future__ import annotations
from pathlib import Path
import re
from scripts.package_release import check_version_consistency
from scripts.sync_eula import render_plain
import version
ROOT=Path(__file__).resolve().parents[1]
DOCS=['README.md','docs/USER_GUIDE.md','docs/UI_MAP.md','docs/WORKFLOWS.md','docs/TROUBLESHOOTING.md','docs/TECHNICAL_OVERVIEW.md','docs/DATA_RETENTION_AND_DELETION.md','docs/DOCUMENTATION_ACCEPTANCE.md','SECURITY.md','DISCLAIMER.md','NOTICE.txt']


def test_documentation_local_links_and_eula_sync():
    for name in DOCS:
        path=ROOT/name
        assert path.is_file()
        for link in re.findall(r'\]\(([^)]+)\)',path.read_text(encoding="utf-8")):
            if link.startswith(('https://','http://','#')):continue
            assert (path.parent/link.split('#')[0]).exists(), (name,link)
    assert render_plain((ROOT/'EULA.md').read_text(encoding='utf-8')) == (ROOT/'EULA.txt').read_text(encoding='utf-8')
    assert check_version_consistency()['all_matched']
    assert version.APP_ID_WINDOWS in (ROOT/'installer/DOCXdodyr.iss').read_text(encoding="utf-8")


def test_offline_help_is_local_and_packaged():
    text=(ROOT/'web/help.html').read_text(encoding="utf-8")
    assert '<html lang="ru">' in text
    assert 'get_safe_diagnostic_report' in text
    assert not re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',text)
    spec=(ROOT/'DOCXdodyr.spec').read_text(encoding="utf-8")
    for filename in ['docs','SECURITY.md','DISCLAIMER.md','NOTICE.txt']:
        assert f"REPO_ROOT / '{filename}'" in spec
