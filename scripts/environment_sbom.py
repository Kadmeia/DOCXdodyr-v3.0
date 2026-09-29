"""Inventory the actual build environment; does not assert redistribution rights."""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from version import __version__


def generate():
    components=[]
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata.get('Name', '').lower()):
        name=dist.metadata.get('Name')
        if not name:
            continue
        license_files=[str(p) for p in (dist.files or []) if any(word in str(p).lower() for word in ('license', 'copying', 'notice'))]
        components.append({'type':'library', 'name':name, 'version':dist.version, 'purl':f'pkg:pypi/{name.lower()}@{dist.version}', 'properties':[
            {'name':'docxdodyr:license-metadata','value':dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'UNKNOWN'},
            {'name':'docxdodyr:license-files','value':json.dumps(license_files)},
            {'name':'docxdodyr:source','value':dist.metadata.get('Home-page') or '; '.join(dist.metadata.get_all('Project-URL') or []) or 'UNKNOWN'},
            {'name':'docxdodyr:redistribution-review','value':'pending'}]})
    return {'bomFormat':'CycloneDX','specVersion':'1.5','version':1,'metadata':{'component':{'type':'application','name':'DOCXdodyr','version':__version__},'properties':[
        {'name':'docxdodyr:scope','value':'build-environment; includes build tools, not an asserted frozen inventory'},
        {'name':'docxdodyr:python','value':platform.python_version()}, {'name':'docxdodyr:architecture','value':platform.machine()}]},'components':components}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(generate(),ensure_ascii=False,indent=2),encoding='utf-8')
