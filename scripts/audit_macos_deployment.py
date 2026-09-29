"""Inspect deployment load commands of every bundled Mach-O."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import json
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.binary_arch import mach_files


def minimum_version(path):
    result = subprocess.run(['otool', '-l', str(path)], check=True, capture_output=True, text=True).stdout
    values=[]; relevant=False
    for line in result.splitlines():
        text=line.strip()
        if text.startswith('cmd '):
            relevant=text in ('cmd LC_BUILD_VERSION', 'cmd LC_VERSION_MIN_MACOSX')
        elif relevant:
            match=re.match(r'(?:minos|version) (\d+(?:\.\d+){1,2})$',text)
            if match:
                values.append(tuple(map(int,match[1].split('.'))))
                relevant=False
    if not values:
        raise ValueError('No macOS minimum version load command')
    return max(values)


def audit(app, target=(14,0)):
    files=list(mach_files(app))
    if not files:
        raise ValueError('No Mach-O files')
    # Serialize native binary readers; removable-volume audits must not fan out.
    versions=[minimum_version(path) for path in files]
    newer=[str(p.relative_to(app)) for p,v in zip(files,versions) if (v+(0,0))[:3] > (target+(0,0))[:3]]
    return {'target':'.'.join(map(str,target)), 'maximum_required':'.'.join(map(str,max(versions))), 'binaries':len(files), 'incompatible_count':len(newer), 'incompatible_files':newer, 'passed':not newer}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('application',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--target',type=str,default='14.0',help='Minimum target macOS version (default: 14.0)')
    args=parser.parse_args()
    target_tuple=tuple(map(int,args.target.split('.')))
    result=audit(args.application,target=target_tuple)
    args.output.write_text(json.dumps(result,indent=2))
    print(f"Inspected {result['binaries']} binaries; maximum minimum OS {result['maximum_required']} (target: {args.target})")
    raise SystemExit(0 if result['passed'] else 1)
