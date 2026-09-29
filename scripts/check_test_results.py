"""Release test gate: a skip/xfail is missing evidence, never success."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def check(path):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    bad = [case for case in cases if any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped'))]
    if not cases or bad:
        raise RuntimeError(f'Required test gate failed: {len(cases)} tests, {len(bad)} failed/error/skipped')
    return len(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    print(f'{check(args.report)} required tests passed without skips')
