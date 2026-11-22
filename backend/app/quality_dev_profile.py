"""Local QA profile only for a frozen Quality Upgrade build.

A Quality Upgrade artifact is recognised by its own executable location:
``<checkout>/quality-upgrade/runs/<build-uuid>/dist/.../PODCUTBackend.exe`` where
``<checkout>`` carries the Multi-PC ``podcut-dev-workspace.json`` marker. Its
writable profile stays inside that run directory, so it never shares the
LocalAppData profile of an installed Phase 3A or Quality Upgrade V1 package.
This module is bundled only into Quality builds and performs no writes.
"""
import json
from pathlib import Path

from .runtime_profile import APP_DIRECTORY, IsolationError, canonical_instance, checked_path

RUNS_DIRECTORY = ('quality-upgrade', 'runs')
WORKSPACE_MARKER = 'podcut-dev-workspace.json'
WORKSPACE_KIND = 'podcut-multipc-dev'
EXECUTABLE_LAYOUT = (
    'dist', 'ClipClip.ai-win32-x64', 'resources', 'podcut', 'backend', 'PODCUTBackend.exe',
)


def quality_runs(executable: Path) -> Path | None:
    """Return the nearest ``quality-upgrade/runs`` ancestor of the executable, if any."""
    if not executable.is_absolute() or '..' in executable.parts:
        raise IsolationError('The packaged executable must be an absolute native path.')
    parts = executable.parts
    for index in range(len(parts) - 2, 0, -1):
        if (parts[index - 1].lower(), parts[index].lower()) == RUNS_DIRECTORY:
            return Path(*parts[:index + 1])
    return None


def _require_workspace(project: Path) -> Path:
    # lab_profile is excluded from frozen builds, so the marker rule is repeated here.
    project = checked_path(project, project, 'Quality Upgrade workspace')
    marker = checked_path(project / WORKSPACE_MARKER, project, 'Quality Upgrade workspace marker')
    try:
        if not marker.is_file() or marker.stat().st_size > 16384:
            raise ValueError()
        data = json.loads(marker.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise IsolationError('Local Quality Upgrade QA requires a Multi-PC workspace marker.') from None
    if not isinstance(data, dict) or type(data.get('schema')) is not int or data.get('schema') != 1 or data.get('kind') != WORKSPACE_KIND:
        raise IsolationError('Local Quality Upgrade QA requires a Multi-PC workspace marker.')
    return project


def quality_profile(executable: Path, instance: str) -> Path | None:
    runs = quality_runs(executable)
    if runs is None:
        return None
    build, *layout = executable.relative_to(runs).parts
    if tuple(layout) != EXECUTABLE_LAYOUT or not canonical_instance(build) or not canonical_instance(instance):
        raise IsolationError('Local Quality Upgrade QA requires a build directory and an isolated profile UUID.')
    project = _require_workspace(runs.parent.parent)
    runs = checked_path(runs, project, 'Quality Upgrade runs')
    run = checked_path(runs / build, runs, 'Quality Upgrade run')
    if checked_path(executable, run, 'Quality Upgrade executable') != run.joinpath(*EXECUTABLE_LAYOUT):
        raise IsolationError('Local Quality Upgrade QA requires a build directory and an isolated profile UUID.')
    return checked_path(run / APP_DIRECTORY / 'profiles' / instance, run, 'Quality Upgrade profile')
