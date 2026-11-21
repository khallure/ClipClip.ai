"""Actual desktop conflict dialog test; close its dialog through the owned UI."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import time
import uuid

_spec = importlib.util.spec_from_file_location('podcut_package_qa', Path(__file__).with_name('verify-phase2b-package.py'))
_qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_qa)
PROJECT, PROFILE = _qa.PROJECT, _qa.PROFILE
checked, process_identity, free_dev_ports = _qa.checked, _qa.process_identity, _qa.free_dev_ports


def main():
    free_dev_ports()
    run = str(uuid.uuid4())
    output = checked(PROFILE/'runtime/phase2b'/('conflict-' + run), PROFILE)
    output.mkdir(parents=True)
    executable = checked(PROJECT/'desktop/dist/PODCUTAI-InstallerDev-win32-x64/PODCUTAI-InstallerDev.exe', PROJECT)
    node = checked(executable.parent/'resources/podcut/node/node.exe', PROJECT)
    script, ready, requests = output/'foreign.cjs', output/'ready', output/'requests.log'
    script.write_text("const fs=require('node:fs'),http=require('node:http');const [ready,requests]=process.argv.slice(2);http.createServer((req,res)=>{fs.appendFileSync(requests,'unexpected HTTP request\\n');res.end('foreign fixture')}).listen(3300,'127.0.0.1',()=>fs.writeFileSync(ready,'ready'));", encoding='utf-8')
    windows = Path(os.environ['SystemRoot'])
    environment = {'SystemRoot': str(windows), 'WINDIR': str(windows), 'OS': 'Windows_NT',
                   'COMSPEC': str(windows/'System32/cmd.exe'), 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
                   'PATH': str(node.parent) + os.pathsep + str(windows/'System32'),
                   'TEMP': str(PROFILE/'temp'), 'TMP': str(PROFILE/'temp'), 'USERPROFILE': str(PROFILE),
                   'APPDATA': str(PROFILE/'config/desktop-qa-appdata'),
                   'LOCALAPPDATA': str(PROFILE/'cache/desktop-qa-appdata')}
    foreign = app = None
    logs = [open(output/(label+'.log'), 'wb') for label in ('node-stdout','node-stderr','app-stdout','app-stderr')]
    report = {'run_id': run, 'status': 'running', 'port': 3300, 'output': str(output)}
    try:
        foreign = subprocess.Popen([str(node), str(script), str(ready), str(requests)], cwd=output,
                                   env=environment, stdout=logs[0], stderr=logs[1], stdin=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        foreign_identity = process_identity(foreign.pid)
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            if foreign.poll() is not None:
                raise RuntimeError('The explicitly owned conflict fixture exited.')
            time.sleep(.05)
        if not ready.is_file():
            raise RuntimeError('The conflict fixture did not become ready.')
        app = subprocess.Popen([str(executable)], cwd=output, env=environment,
                               stdout=logs[2], stderr=logs[3], stdin=subprocess.DEVNULL)
        report.update(app_pid=app.pid, foreign_pid=foreign.pid)
        (PROFILE/'runtime/phase2b/ui-conflict-active.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('WAIT_DIALOG: close only the PODCUT Installer DEV conflict message.', flush=True)
        app.wait(timeout=100)
        report.update(exit_code=app.returncode,
                      foreign_preserved=foreign.poll() is None and process_identity(foreign.pid) == foreign_identity,
                      no_credential_request=not requests.exists())
        records = []
        for file in (PROFILE/'runtime/desktop').glob('*.json'):
            value = json.loads(file.read_text(encoding='utf-8-sig'))
            if value.get('desktop_pid') == app.pid:
                records.append(value)
        report['runtime_records'] = records
        report['passed'] = (app.returncode == 0 and report['foreign_preserved'] and report['no_credential_request']
                            and len(records) == 1 and records[0]['status'] == 'stopped'
                            and records[0]['host_pid'] is None and not records[0]['services'])
        report['status'] = 'passed' if report['passed'] else 'failed'
    except Exception as error:
        report.update(status='failed', passed=False, error=type(error).__name__ + ': ' + str(error))
    finally:
        # Only these exact Popen handles; never stop an occupant discovered by port.
        for process in (app, foreign):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
        for stream in logs:
            stream.close()
        (output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (PROFILE/'runtime/phase2b/ui-conflict-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    return 0 if report.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
