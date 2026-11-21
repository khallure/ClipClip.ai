"""Read-only package/source comparison; write evidence only in the DEV profile."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import xml.etree.ElementTree as ET

_spec = importlib.util.spec_from_file_location('podcut_package_qa', Path(__file__).with_name('verify-phase2b-package.py'))
_qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_qa)
PROJECT, PROFILE = _qa.PROJECT, _qa.PROFILE


def main():
    output = _qa.checked(PROFILE/'runtime/phase2b', PROFILE)
    manifest = json.loads((output/'desktop-package-manifest.json').read_text(encoding='utf-8-sig'))
    package = _qa.checked(Path(manifest['output']), PROJECT)
    current = _qa.inventory(package)
    expected = {row['path']: row for row in manifest['files']}
    changed = sorted(name for name in current.keys() | expected.keys()
                     if name not in current or name not in expected
                     or current[name]['sha256'].lower() != expected[name]['sha256'].lower()
                     or current[name]['size'] != expected[name]['bytes'])
    unwanted = [name for name in current
                if Path(name).name.startswith('.env') or Path(name).name == 'model.bin'
                or '/backend/data/' in '/' + name or '/.venv/' in '/' + name]
    notices = [name for name in current if name.startswith('resources/podcut/licenses/')]
    report = {'package_files': len(current), 'package_bytes': sum(row['size'] for row in current.values()),
              'manifest_matches_after_all_UI_tests': not changed, 'changed_package_paths': changed,
              'unwanted_payload_files': unwanted, 'license_notice_files': len(notices)}

    # Use current complete suites only. Older job-host runs contain one test
    # renamed by the shutdown fix and must not inflate the current case count.
    runs = ['regression-junit.xml', 'boundaries-junit.xml', 'shutdown-final-junit.xml']
    cases, suites = {}, []
    for name in runs:
        root = ET.parse(output/name).getroot()
        values = []
        for case in root.iter('testcase'):
            status = 'failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
            cases[(case.attrib.get('classname'), case.attrib['name'])] = status
            values.append(status)
        suites.append({'file': name, **{key: values.count(key) for key in ('passed','failed','skipped')}})
    report['backend_tests'] = {key: list(cases.values()).count(key) for key in ('passed','failed','skipped')}
    report['junit_inputs'] = suites
    report['previously_skipped'] = [{**case.attrib, 'passed': case.find('skipped') is None and case.find('failure') is None and case.find('error') is None}
                                  for case in ET.parse(output/'prior-skips-junit.xml').getroot().iter('testcase')]

    baseline = json.loads((PROJECT/'PHASE1-SOURCE-CHECKSUMS.json').read_text(encoding='utf-8-sig'))
    phase2a = json.loads((PROFILE/'runtime/phase2a/development-source-changes.json').read_text(encoding='utf-8-sig'))
    overrides = {row['path']: row['after_sha256'] for row in phase2a['changes']}
    source_changes = []
    for row in baseline['files']:
        name = row['path']
        before = overrides.get(name, row['sha256']).lower()
        file = _qa.checked(PROJECT/name, PROJECT)
        after = _qa.sha(file)
        if before != after:
            source_changes.append({'path': name, 'phase2a_sha256': before, 'phase2b_sha256': after})
    report['modified_baseline_files_since_phase2a'] = source_changes
    preserved = ['backend/app/analysis.py', 'backend/app/captions.py', 'backend/app/caption_state.py',
                 'backend/app/reframe_detection.py', 'backend/app/reframe_render.py',
                 'backend/app/reframe_models.py', 'backend/app/reframe_store.py',
                 'backend/app/active_speaker_policy.py', 'frontend/components/ReframeEditor.tsx',
                 'frontend/components/PoppinsCaptions.tsx']
    expected_sources = {row['path']: overrides.get(row['path'], row['sha256']).lower() for row in baseline['files']}
    report['unchanged_algorithm_examples'] = {name: _qa.sha(PROJECT/name) == expected_sources[name]
                                              for name in preserved if name in expected_sources and (PROJECT/name).is_file()}
    scans = []
    for root in (PROJECT, PROFILE):
        _qa.checked(root, root)
        pending, file_count, directory_count, linked = [root], 0, 0, []
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        linked.append(str(Path(entry.path).relative_to(root)))
                    elif stat.S_ISDIR(info.st_mode):
                        directory_count += 1
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        file_count += 1
                        if info.st_nlink > 1:
                            linked.append(str(Path(entry.path).relative_to(root)))
        scans.append({'root': str(root), 'files': file_count, 'directories': directory_count, 'linked_entries': linked})
    report['filesystem_isolation'] = scans
    report['passed'] = (not changed and not unwanted and report['backend_tests']['failed'] == 0
                        and report['backend_tests']['skipped'] == 0
                        and not any(row['linked_entries'] for row in scans))
    (output/'final-package-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
