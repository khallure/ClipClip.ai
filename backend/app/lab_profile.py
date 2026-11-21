"""Source-only Multi-PC workspace boundary. Excluded from frozen distributions.

The source checkout is identified by its own location plus the
``podcut-dev-workspace.json`` marker, never by a machine-specific path. The
writable development profile is ``<checkout>/.dev/profile``, the same contract
used by ``scripts/common.ps1``. This module only reads; it never creates files.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

WORKSPACE_MARKER = 'podcut-dev-workspace.json'
WORKSPACE_KIND = 'podcut-multipc-dev'
OWNER_MARKER = Path('config') / 'workspace-owner.json'
MAX_MARKER_BYTES = 16384
# Same canonical lowercase UUID rule as scripts/common.ps1.
WORKSPACE_ID = re.compile(r'[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}')


class WorkspaceError(RuntimeError):
    """The source checkout is not a valid Multi-PC development workspace."""


def _checked_path(value: str | Path, root: Path, label: str) -> Path:
    """Check containment and reject links, junctions, hardlinks or alternate streams."""
    candidate = Path(value)
    if not candidate.is_absolute() or '..' in candidate.parts:
        raise WorkspaceError(f'{label} must be an absolute path without parent traversal.')
    candidate = Path(os.path.abspath(candidate))
    boundary = Path(os.path.abspath(root))
    if os.name == 'nt' and (not re.fullmatch(r'[A-Za-z]:\\', candidate.anchor) or ':' in str(candidate)[2:]):
        raise WorkspaceError(f'{label} must use a local Windows drive path without alternate streams.')
    try:
        candidate.relative_to(boundary)
    except ValueError:
        raise WorkspaceError(f'{label} is outside its approved workspace root.') from None
    for ancestor in (candidate, *candidate.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise WorkspaceError(f'{label} contains a link, junction or reparse point.')
        if stat.S_ISREG(info.st_mode) and getattr(info, 'st_nlink', 1) > 1:
            raise WorkspaceError(f'{label} contains a hardlinked file.')
    if candidate.resolve() != candidate:
        raise WorkspaceError(f'{label} does not resolve to its approved workspace path.')
    return candidate


def _read_marker(path: Path, root: Path, label: str) -> dict:
    """Read a small JSON marker that must be a regular, unlinked file inside ``root``."""
    path = _checked_path(path, root, label)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise WorkspaceError(f'{label} is missing.') from None
    if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_nlink', 1) > 1
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or info.st_size > MAX_MARKER_BYTES):
        raise WorkspaceError(f'{label} must be a small regular file without links.')
    try:
        # utf-8-sig accepts a PowerShell byte order mark without accepting other encodings.
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise WorkspaceError(f'{label} could not be read.') from None
    if not isinstance(data, dict):
        raise WorkspaceError(f'{label} is invalid.')
    return data


def resolve_workspace(project: str | Path) -> tuple[Path, Path]:
    """Return ``(project, profile)`` for a marked Multi-PC checkout."""
    project_path = Path(project)
    project_root = _checked_path(project_path, project_path, 'Project root')
    marker = _read_marker(project_root / WORKSPACE_MARKER, project_root, 'Multi-PC workspace marker')
    if type(marker.get('schema')) is not int or marker.get('schema') != 1 or marker.get('kind') != WORKSPACE_KIND:
        raise WorkspaceError('The source checkout is not a Multi-PC development workspace.')
    profile_root = _checked_path(project_root / '.dev' / 'profile', project_root, 'Profile root')
    verify_profile_owner(project_root, profile_root)
    return project_root, profile_root


def verify_profile_owner(project: Path, profile: Path) -> None:
    """Mirror ``Initialize-DevProfile``: a profile with data must belong to this checkout.

    An absent or empty profile is accepted here because the launcher creates the
    ownership marker; the runtime still requires ``config/backend.env`` later.
    """
    project = _checked_path(project, project, 'Project root')
    profile = _checked_path(profile, project, 'Profile root')
    try:
        info = profile.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise WorkspaceError('The DEV profile must be a plain directory inside this checkout.')
    entries = list(profile.iterdir())
    if not entries:
        return
    owner_file = _checked_path(profile / OWNER_MARKER, profile, 'DEV profile ownership marker')
    if not os.path.lexists(owner_file):
        config = _checked_path(profile / 'config', profile, 'DEV profile configuration directory')
        # The launcher may leave only an empty config folder after an interrupted first run.
        if len(entries) == 1 and entries[0] == config and config.is_dir() and not any(config.iterdir()):
            return
        raise WorkspaceError('The DEV profile contains data without an ownership marker.')
    owner = _read_marker(owner_file, profile, 'DEV profile ownership marker')
    owned_project = owner.get('project_root')
    owned_profile = owner.get('profile_root')
    workspace_id = owner.get('workspace_id')
    if (type(owner.get('schema')) is not int or owner.get('schema') != 1
            or not isinstance(workspace_id, str) or not WORKSPACE_ID.fullmatch(workspace_id)
            or not isinstance(owned_project, str) or not isinstance(owned_profile, str)
            or not Path(owned_project).is_absolute() or not Path(owned_profile).is_absolute()
            or '..' in Path(owned_project).parts or '..' in Path(owned_profile).parts):
        raise WorkspaceError('The DEV profile ownership marker is invalid.')
    # Case-insensitive comparison matches PowerShell while rejecting foreign checkouts or trailing slashes.
    if (owned_project.lower() != str(project).lower() or owned_profile.lower() != str(profile).lower()
            or Path(owned_project) != project or Path(owned_profile) != profile):
        raise WorkspaceError('The DEV profile belongs to a different checkout.')


APPROVED_PROJECT, APPROVED_PROFILE = resolve_workspace(Path(__file__).absolute().parents[2])
