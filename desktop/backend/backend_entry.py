"""PyInstaller entry: explicit modes prevent frozen recursive Python launches."""
from __future__ import annotations

import multiprocessing
import json
import os
import sys
import uuid


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('This entry point must run from the packaged onedir executable.')
    if arguments[:1] == ['--bootstrap-info']:
        from app.runtime_profile import bootstrap_info, canonical_instance
        if arguments == ['--bootstrap-info']:
            instance = ''
        elif len(arguments) == 3 and arguments[1] == '--isolated-profile':
            instance = canonical_instance(arguments[2])
        else:
            raise RuntimeError('Invalid native bootstrap command.')
        print(json.dumps(bootstrap_info(instance), ensure_ascii=True, separators=(',', ':')), flush=True)
        return 0
    if arguments[:1] == ['--job-host']:
        # Deliberately before app.config, model/native libraries or server state.
        from app.desktop_jobhost import main as job_host_main
        return job_host_main(arguments[1:])
    if len(arguments) == 3 and arguments[:2] == ['--worker', 'ingestion']:
        from app import config
        config.RUNTIME.validate_writable(arguments[2], 'Ingestion worker request')
        from app.process_runtime import reset_worker_dll_directory
        reset_worker_dll_directory()
        from app.ingestion_worker import main as worker_main
        return worker_main([arguments[2]])
    if len(arguments) not in {3, 5} or arguments[:2] != ['--serve', '--run-id']:
        raise RuntimeError('Expected a desktop server, job-host or private worker command.')
    from app.runtime_profile import canonical_instance
    instance = ''
    if len(arguments) == 5:
        if arguments[3] != '--isolated-profile':
            raise RuntimeError('Invalid isolated profile command.')
        instance = canonical_instance(arguments[4])
    if os.environ.get('PODCUT_PROFILE_INSTANCE', '') != instance:
        raise RuntimeError('The server command and environment profile identities differ.')
    try:
        uuid.UUID(arguments[2])
    except ValueError:
        raise RuntimeError('The desktop server run ID is invalid.') from None
    if arguments[2] != os.environ.get('PODCUT_RUN_ID'):
        raise RuntimeError('The server command and environment run identities differ.')
    from app import config
    if not config.RUNTIME.packaged or config.RUNTIME.run_id != arguments[2]:
        raise RuntimeError('The server did not load its packaged runtime identity.')
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=8300, workers=1,
                loop='asyncio', http='h11', ws='none', reload=False, access_log=True,
                log_level='info', use_colors=False)
    return 0


if __name__ == '__main__':
    # PyInstaller's multiprocessing override must divert its own helper command
    # before ordinary argv parsing and all heavyweight application imports.
    multiprocessing.freeze_support()
    raise SystemExit(main())
