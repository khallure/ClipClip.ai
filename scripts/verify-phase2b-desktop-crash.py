"""Terminate only our new desktop handle; verify its job/children close on EOF."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

_spec = importlib.util.spec_from_file_location('podcut_package_qa', Path(__file__).with_name('verify-phase2b-package.py'))
_qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_qa)
PROJECT, PROFILE = _qa.PROJECT, _qa.PROFILE


def main():
    _qa.free_dev_ports()
    run = str(uuid.uuid4())
    output = _qa.checked(PROFILE/'runtime/phase2b'/('desktop-crash-' + run), PROFILE)
    output.mkdir(parents=True)
    executable = _qa.checked(PROJECT/'desktop/dist/PODCUTAI-InstallerDev-win32-x64/PODCUTAI-InstallerDev.exe', PROJECT)
    windows = Path(os.environ['SystemRoot'])
    environment = {'SystemRoot': str(windows), 'WINDIR': str(windows), 'OS': 'Windows_NT',
                   'COMSPEC': str(windows/'System32/cmd.exe'), 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
                   'PATH': str(executable.parent) + os.pathsep + str(windows/'System32'),
                   'TEMP': str(PROFILE/'temp'), 'TMP': str(PROFILE/'temp'), 'USERPROFILE': str(PROFILE),
                   'APPDATA': str(PROFILE/'config/desktop-qa-appdata'),
                   'LOCALAPPDATA': str(PROFILE/'cache/desktop-qa-appdata')}
    report = {'run_id': run, 'output': str(output), 'passed': False}
    app = None
    identities = {}
    logs = [open(output/(label+'.log'), 'wb') for label in ('stdout', 'stderr')]
    try:
        app = subprocess.Popen([str(executable)], cwd=output, env=environment, stdin=subprocess.DEVNULL,
                               stdout=logs[0], stderr=logs[1])
        identity = _qa.process_identity(app.pid)
        report['desktop'] = identity
        deadline = time.monotonic() + 75
        record = None
        while time.monotonic() < deadline:
            if app.poll() is not None:
                raise RuntimeError('The explicitly owned desktop exited before readiness.')
            for file in (PROFILE/'runtime/desktop').glob('*.json'):
                value = json.loads(file.read_text(encoding='utf-8-sig'))
                if value.get('desktop_pid') == app.pid and value.get('status') == 'ready':
                    record = value
                    break
            if record:
                break
            time.sleep(.2)
        if not record:
            raise RuntimeError('The explicitly owned desktop did not become ready.')
        identities = {role: _qa.process_identity(pid) for role, pid in record['services'].items()}
        identities['host'] = _qa.process_identity(record['host_pid'])
        if not all(identities.values()):
            raise RuntimeError('An explicitly owned service identity could not be observed.')
        if any(not Path(value['executable']).is_relative_to(executable.parent) for value in identities.values()):
            raise RuntimeError('A service was outside this independent desktop package.')
        descendants = _qa.service_descendants({'desktop': identity})
        report.update(runtime_ready=record, services=identities, descendants_before=descendants)
        if _qa.process_identity(app.pid) != identity:
            raise RuntimeError('Our desktop identity changed before the crash simulation.')
        # TerminateProcess uses this exact retained Popen handle, never port/name/PID discovery.
        app.terminate()
        app.wait(timeout=10)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            remaining = [value for value in [*identities.values(), *descendants]
                         if _qa.process_identity(value['pid']) == {key: value[key] for key in ('pid','executable','created_ticks')}]
            if not remaining:
                break
            time.sleep(.1)
        _qa.free_dev_ports()
        report.update(exit_code=app.returncode, remaining_owned_descendants=remaining,
                      dev_ports_released=True, passed=not remaining)
        # The killed desktop cannot rewrite its last runtime JSON. PID identity, not
        # that stale file, proves the process and its job have really gone away.
        report['stale_ready_record_expected_after_termination'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if app is not None and app.poll() is None:
            app.terminate()
            app.wait(timeout=10)
        for stream in logs:
            stream.close()
        report['status'] = 'passed' if report['passed'] else 'failed'
        (output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (PROFILE/'runtime/phase2b/ui-crash-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
