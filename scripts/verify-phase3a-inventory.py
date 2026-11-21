"""Compare two complete application directories with the release manifest, read-only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import types
from PyInstaller.archive.readers import CArchiveReader

spec = importlib.util.spec_from_file_location('qa_paths', Path(__file__).with_name('verify-phase2b-package.py'))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def strings(code):
    yield code.co_filename
    for value in code.co_consts:
        if isinstance(value, str):
            yield value
        elif isinstance(value, types.CodeType):
            yield from strings(value)


def main():
    evidence = qa.checked(qa.PROFILE/'runtime/phase3a', qa.PROFILE)
    manifest = json.loads((evidence/'desktop-package-manifest.json').read_text(encoding='utf8'))
    expected = {row['path']: row for row in manifest['files']}
    relocated = qa.checked(Path(sys.argv[1]), qa.PROJECT)
    primary = qa.checked(Path(manifest['output']), qa.PROJECT)
    result = {'files_per_package':len(expected),'bytes':manifest['total_bytes'],'comparisons':[], 'path_references':[]}
    # These needles identify this lab/user/original in absolute paths in shipping files.
    fragments = ['podcut-labs','directory - pc','users/video/','users'+chr(92)+'video'+chr(92)]
    needles = [s.encode(codec) for s in fragments for codec in ('utf8','utf-16le')]
    for root in [primary,relocated]:
        different, found, pending = [], set(), [root]
        while pending:
            for entry in os.scandir(pending.pop()):
                file = Path(entry.path)
                info = file.lstat()  # Windows DirEntry.stat omits the native link count.
                if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or not entry.is_dir() and info.st_nlink != 1:
                    raise RuntimeError('Unexpected link in final package.')
                if entry.is_dir():
                    pending.append(file)
                    continue
                name = file.relative_to(root).as_posix()
                found.add(name)
                hasher = hashlib.sha256()
                tail = b''
                with file.open('rb') as stream:
                    while chunk := stream.read(2**20):
                        hasher.update(chunk)
                        if root == primary:
                            block = tail+chunk.lower()
                            if any(value in block for value in needles):
                                if name not in result['path_references']:
                                    result['path_references'].append(name)
                            tail = block[-80:]
                if hasher.hexdigest() != expected.get(name,{}).get('sha256'):
                    different.append(name)
        result['comparisons'].append({'root':str(root),'differing':different,'missing':sorted(expected.keys()-found)})
    exe = CArchiveReader(str(primary/'resources/podcut/backend/PODCUTBackend.exe'))
    pyz = exe.open_embedded_archive(next(name for name in exe.toc if name.endswith('.pyz')))
    modules = [name for name in pyz.toc if name == 'app' or name.startswith('app.')]
    for name in modules:
        if any(frag in item.lower() for item in strings(pyz.extract(name)) for frag in fragments):
            result['path_references'].append(name)
    result['application_modules_checked']=len(modules)
    result['source_only_lab_module_absent']='app.lab_profile' not in pyz.toc
    result['passed']=not result['path_references'] and result['source_only_lab_module_absent'] and not any(
        row['differing'] or row['missing'] for row in result['comparisons'])
    (evidence/'final-inventory.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
