"""Local, redacted static scan; not a claim of exhaustive PII or binary/history coverage."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 5 * 1024 * 1024
RULES = {
    'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'aws-access-key': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'github-token': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{60,255})\b',
    'google-api-key': r'\bAIza[0-9A-Za-z_-]{35}\b',
    'openai-like-key': r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b',
    'personal-absolute-path': r'(?:/Users|/home)/[A-Za-z0-9_.-]+/',
    'personal-cloud-path': r'CloudStorage/(?:GoogleDrive|OneDrive)',
}


def text_findings(name, raw):
    """Return positions only; never include matched text in evidence."""
    if b'\0' in raw:
        raise UnicodeError()
    content = raw.decode('utf-8')
    return [
        {'file': name, 'line': content.count('\n', 0, match.start()) + 1, 'rule': rule}
        for rule, pattern in RULES.items()
        for match in re.finditer(pattern, content)
    ]


def scan_history(root):
    """Scan unique regular blobs reachable from local refs, without extraction.

    No fetch, worktree checkout or history rewrite. Large and binary blobs are
    excluded explicitly; regex absence is not proof of absence of personal data.
    """
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)

    commits = git('rev-list', '--all').decode('ascii').splitlines()
    seen = set()
    findings, skipped = [], []
    scanned = 0
    for commit in commits:
        for record in git('ls-tree', '-r', '-z', commit).split(b'\0'):
            if not record:
                continue
            header, path = record.split(b'\t', 1)
            mode, kind, oid = header.decode('ascii').split()
            name = path.decode('utf-8', errors='replace')
            identity = (mode, oid, name)
            if identity in seen:
                continue
            seen.add(identity)
            reference = {'file': name, 'commit': commit, 'object': oid}
            if kind != 'blob' or mode not in ('100644', '100755'):
                skipped.append({**reference, 'reason': 'non-regular-entry'})
                continue
            if int(git('cat-file', '-s', oid)) > MAX_BYTES:
                skipped.append({**reference, 'reason': 'over-5MB'})
                continue
            raw = git('cat-file', 'blob', oid)
            try:
                matches = text_findings(name, raw)
            except UnicodeError:
                skipped.append({**reference, 'reason': 'binary-or-non-UTF8; contents not extracted'})
                continue
            scanned += 1
            findings.extend({**reference, **match} for match in matches)
    return {'scope': 'unique (mode, blob, path) across commits reachable from local refs; no reflogs/unreachable objects',
            'commits': len(commits), 'text_versions_scanned': scanned,
            'findings': findings, 'skipped': skipped, 'exhaustive_pii_scan': False}


def scan():
    names = sorted(set(subprocess.check_output(
        ['git', 'ls-files', '-c', '-o', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')) - {''})
    findings = []
    skipped = []
    inventory = {}
    for name in names:
        path = ROOT/name
        if (not path.resolve().is_relative_to(ROOT.resolve()) or
                not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES):
            skipped.append({'file': name, 'reason': 'missing/symlink/over-5MB'})
            continue
        raw = path.read_bytes()
        inventory[name] = hashlib.sha256(raw).hexdigest()
        try:
            matches = text_findings(name, raw)
        except UnicodeError:
            skipped.append({'file': name, 'reason': 'binary-or-non-UTF8; contents not processed'})
            continue
        findings.extend(matches)
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    return {'scope': 'tracked + nonignored working-tree files, UTF-8 text <=5MB; no document extraction',
            'exhaustive_pii_scan': False, 'git_history_scanned': False,
            'inventory_digest': digest, 'files_hashed': len(inventory),
            'findings': findings, 'skipped': skipped, 'rules': list(RULES)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--history', action='store_true', help='Also scan reachable local history without checkout/fetch')
    args = parser.parse_args()
    report = scan()
    if args.history:
        report['history'] = scan_history(ROOT)
        report['git_history_scanned'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    credential_hits = [f for f in report['findings'] if not f['rule'].startswith('personal-')]
    print(json.dumps({'files_hashed': report['files_hashed'], 'credential_candidates': len(credential_hits),
                      'path_candidates': len(report['findings'])-len(credential_hits), 'skipped': len(report['skipped'])}))
