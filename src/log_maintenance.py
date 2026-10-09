"""Organize local logs and expire completed diagnostics (dry-run by default).

    python -m src.log_maintenance migrate
    python -m src.log_maintenance migrate --apply
    python -m src.log_maintenance cleanup --apply

Backups are moved to data/backups/recovery/, never expired automatically.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import config
from src.obs import PipelineLogger


@dataclass(frozen=True)
class Action:
    source: Path
    destination: Path | None = None


_TEST_PREFIXES = (
    'recovery-tests', 'recovery-final-tests', 'recovery-full-tests',
    'post-cleanup-tests', 'observability-tests',
)
_RETIRED_HELPERS = {'cleanup_obsolete_local.py', 'verify_observability.py'}
_DAILY_LOG = re.compile(r'pipeline-\d{4}-\d{2}-\d{2}\.jsonl')


def migration_plan(root: Path = config.ROOT_DIR) -> list[Action]:
    root = root.resolve()
    logs = root / 'logs'
    if not logs.exists():
        return []
    actions = []
    for path in sorted(logs.iterdir()):
        if path.name in {'pipeline', 'maintenance'}:
            continue
        if path.is_dir():
            if path.name.startswith(_TEST_PREFIXES):
                actions.append(Action(path))
            elif path.name.startswith('recovery-v6-'):
                stamp = path.name.removeprefix('recovery-v6-')
                if any((path / name).exists() for name in ('index-before', 'normalized-before')):
                    destination = root / 'data/backups/recovery' / stamp
                else:
                    destination = logs / 'maintenance/recovery' / stamp
                actions.append(Action(path, destination))
        elif path.name in _RETIRED_HELPERS:
            actions.append(Action(path))
        elif _DAILY_LOG.fullmatch(path.name):
            actions.append(Action(path, logs / 'pipeline' / path.name))
        elif path.suffix in {'.log', '.json', '.yaml', '.diff'}:
            category = 'recovery' if path.name.startswith('recovery-') or path.name == 'corpus_manifest_v6.yaml' else 'checks'
            actions.append(Action(path, logs / 'maintenance' / category / 'legacy' / path.name))
    return actions


def _latest_modified(path: Path) -> datetime:
    files = [p for p in path.rglob('*') if p.is_file()]
    stamp = max((p.stat().st_mtime for p in files), default=path.stat().st_mtime)
    return datetime.fromtimestamp(stamp).astimezone()


def cleanup_plan(
    root: Path = config.ROOT_DIR, *, now: datetime | None = None,
    pipeline_days: int | None = None, maintenance_days: int | None = None,
) -> list[Action]:
    root = root.resolve()
    now = now or datetime.now().astimezone()
    pipeline_days = config.LOG_RETENTION_DAYS if pipeline_days is None else pipeline_days
    maintenance_days = config.MAINTENANCE_RETENTION_DAYS if maintenance_days is None else maintenance_days
    if min(pipeline_days, maintenance_days) < 0:
        raise ValueError('Retention days must be nonnegative')
    actions = []
    for directory in (root / 'logs/pipeline', root / 'logs'):
        logger = PipelineLogger(directory, retention_days=pipeline_days)
        actions.extend(Action(path) for path in logger.expired_paths(now))
    if not maintenance_days:
        return actions
    cutoff = now - timedelta(days=maintenance_days)
    for category in ('recovery', 'checks'):
        parent = root / 'logs/maintenance' / category
        if not parent.exists():
            continue
        for run in parent.iterdir():
            if not run.is_dir() or run.is_symlink():
                continue
            state_path = run / 'state.json'
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding='utf-8'))
                except (ValueError, OSError):
                    continue
                # Never expire an explicitly unfinished execution.
                if state.get('stage') not in {'completed', 'failed'}:
                    continue
            if _latest_modified(run) < cutoff:
                actions.append(Action(run))
    temporary = root / '.tmp/tests'
    if temporary.exists():
        for run in temporary.iterdir():
            marker = run / '.pytest-run.json'
            if not run.is_dir() or run.is_symlink() or not marker.is_file():
                continue
            try:
                completed = json.loads(marker.read_text(encoding='utf-8'))
                finished = datetime.fromisoformat(completed['finished_at'])
            except (KeyError, ValueError, OSError):
                continue
            if completed.get('completed') and finished.tzinfo and finished < cutoff:
                actions.append(Action(run))
    return actions


def _snapshot(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError(f'Refusing symbolic link: {path}')
    files = [path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file())
    if any(p.is_symlink() for p in files):
        raise ValueError(f'Refusing a tree containing symbolic links: {path}')
    return {p.name if path.is_file() else p.relative_to(path).as_posix():
            hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def _tracked(root: Path) -> set[str]:
    if not (root / '.git').exists():
        return set()
    return set(subprocess.check_output(['git', 'ls-files'], cwd=root, text=True).splitlines())


def apply_plan(actions: list[Action], root: Path = config.ROOT_DIR) -> None:
    """Validate the entire plan first; moves must preserve every file hash."""
    root = root.resolve()
    tracked = _tracked(root)
    snapshots = {}
    for action in actions:
        source = action.source.resolve()
        if not source.is_relative_to(root):
            raise ValueError('Source must be inside the repository')
        relative = source.relative_to(root).as_posix()
        if not (relative.startswith('logs/') or relative.startswith('.tmp/tests/')):
            raise ValueError('Only log and test-temporary sources may be managed')
        if any(p == relative or p.startswith(relative + '/') for p in tracked):
            raise ValueError(f'Refusing tracked artifact: {relative}')
        snapshots[action.source] = _snapshot(action.source)
        if action.destination is not None:
            destination = action.destination.resolve()
            if not destination.is_relative_to(root):
                raise ValueError('Destination must be inside the repository')
            target = destination.relative_to(root).as_posix()
            if not target.startswith(('logs/pipeline/', 'logs/maintenance/', 'data/backups/recovery/')):
                raise ValueError('Unsupported destination')
            if destination.exists():
                raise FileExistsError(destination)
    for action in actions:
        if action.destination is None:
            if action.source.is_dir():
                shutil.rmtree(action.source)
            else:
                action.source.unlink()
        else:
            action.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(action.source), str(action.destination))
            if _snapshot(action.destination) != snapshots[action.source]:
                shutil.move(str(action.destination), str(action.source))
                raise RuntimeError('Migration changed file hashes; original path restored')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('migrate', 'cleanup'))
    parser.add_argument('--apply', action='store_true', help='apply the plan; default is read-only')
    args = parser.parse_args(argv)
    actions = migration_plan() if args.command == 'migrate' else cleanup_plan()
    summary = [{'action': 'move' if a.destination else 'delete',
                'source': a.source.relative_to(config.ROOT_DIR).as_posix(),
                'destination': a.destination.relative_to(config.ROOT_DIR).as_posix() if a.destination else None}
               for a in actions]
    if args.apply:
        apply_plan(actions)
    print(json.dumps({'applied': args.apply, 'actions': summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
