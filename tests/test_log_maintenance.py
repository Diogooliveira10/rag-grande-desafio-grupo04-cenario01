from datetime import datetime, timedelta, timezone
from importlib import util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import log_maintenance as maintenance
from src import obs, trace


def write(path, content='example'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def test_migration_moves_logs_reports_and_preserves_backup_bytes(tmp_path):
    daily = write(tmp_path / 'logs/pipeline-2026-10-07.jsonl', '{"query_id":"q_1"}\n')
    snapshot = write(tmp_path / 'logs/recovery-v6-2026-10-07_21-00-21/index-before/vectors.jsonl', 'original vectors')
    diagnostic = write(tmp_path / 'logs/recovery-v6-2026-10-07_21-18-41/state.json', '{"stage":"completed"}')
    report = write(tmp_path / 'logs/observability-check.json', '{"checks_passed":true}')
    temp = write(tmp_path / 'logs/post-cleanup-tests-temp/test_1/fixture.txt')
    helper = write(tmp_path / 'logs/verify_observability.py', 'obsolete helper')
    unknown = write(tmp_path / 'logs/custom-script.py', 'keep unknown helper')
    plan = maintenance.migration_plan(tmp_path)
    # Planning is strictly read-only.
    assert all(p.exists() for p in (daily, snapshot, diagnostic, report, temp, helper))
    maintenance.apply_plan(plan, tmp_path)
    assert (tmp_path / 'logs/pipeline' / daily.name).read_text() == '{"query_id":"q_1"}\n'
    assert (tmp_path / 'data/backups/recovery/2026-10-07_21-00-21/index-before/vectors.jsonl').read_text() == 'original vectors'
    assert (tmp_path / 'logs/maintenance/recovery/2026-10-07_21-18-41/state.json').is_file()
    assert (tmp_path / 'logs/maintenance/checks/legacy/observability-check.json').is_file()
    assert not temp.exists() and not helper.exists()
    assert unknown.exists()
    assert maintenance.migration_plan(tmp_path) == []


def test_migration_refuses_collision_before_deleting_anything(tmp_path):
    temp = write(tmp_path / 'logs/recovery-tests-temp/fixture.txt')
    original = write(tmp_path / 'logs/pipeline-2026-10-07.jsonl', 'old')
    destination = write(tmp_path / 'logs/pipeline/pipeline-2026-10-07.jsonl', 'new')
    with pytest.raises(FileExistsError):
        maintenance.apply_plan(maintenance.migration_plan(tmp_path), tmp_path)
    assert temp.exists() and original.read_text() == 'old' and destination.read_text() == 'new'


def test_migration_refuses_tracked_files(tmp_path, monkeypatch):
    source = write(tmp_path / 'logs/report.json')
    monkeypatch.setattr(maintenance, '_tracked', lambda root: {'logs/report.json'})
    with pytest.raises(ValueError, match='tracked'):
        maintenance.apply_plan([maintenance.Action(source)], tmp_path)
    assert source.exists()


def test_backup_and_outside_paths_cannot_be_deleted(tmp_path):
    backup = write(tmp_path / 'data/backups/recovery/original.txt')
    with pytest.raises(ValueError, match='Only log'):
        maintenance.apply_plan([maintenance.Action(backup)], tmp_path)
    outside = write(tmp_path.parent / (tmp_path.name + '-outside.txt'))
    try:
        with pytest.raises(ValueError, match='inside'):
            maintenance.apply_plan([maintenance.Action(outside)], tmp_path)
    finally:
        outside.unlink()
    assert backup.exists()


def test_cleanup_expires_only_old_completed_diagnostics(tmp_path):
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    old = now - timedelta(days=15)
    complete = write(tmp_path / 'logs/maintenance/recovery/old/state.json', '{"stage":"completed"}')
    active = write(tmp_path / 'logs/maintenance/recovery/active/state.json', '{"stage":"prepare"}')
    recent = write(tmp_path / 'logs/maintenance/checks/recent/report.json')
    backup = write(tmp_path / 'data/backups/recovery/original/vectors.jsonl')
    failed = write(tmp_path / '.tmp/tests/failed/.pytest-run.json', json.dumps({
        'completed': True, 'finished_at': old.isoformat(), 'exitstatus': 1,
    }))
    unfinished = write(tmp_path / '.tmp/tests/unfinished/fixture.txt')
    for p in (complete, active, backup):
        os.utime(p, (old.timestamp(), old.timestamp()))
    os.utime(recent, (now.timestamp(), now.timestamp()))
    plan = maintenance.cleanup_plan(tmp_path, now=now, pipeline_days=30, maintenance_days=14)
    assert {a.source for a in plan} == {complete.parent, failed.parent}
    maintenance.apply_plan(plan, tmp_path)
    assert all(p.exists() for p in (active, recent, backup, unfinished))


def test_cleanup_disabled_retention_preserves_all_files(tmp_path):
    old = write(tmp_path / 'logs/pipeline/pipeline-2000-01-01.jsonl')
    assert maintenance.cleanup_plan(tmp_path, pipeline_days=0, maintenance_days=0) == []
    assert old.exists()


def test_cli_is_dry_run_by_default(tmp_path, monkeypatch, capsys):
    source = write(tmp_path / 'logs/report.json')
    monkeypatch.setattr(maintenance.config, 'ROOT_DIR', tmp_path)
    monkeypatch.setattr(maintenance, 'migration_plan', lambda: [maintenance.Action(source)])
    assert maintenance.main(['migrate']) == 0
    assert json.loads(capsys.readouterr().out)['applied'] is False
    assert source.exists()


def test_daily_retention_ignores_other_files_and_keeps_boundary(tmp_path):
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    expired = write(tmp_path / 'pipeline-2026-09-06.jsonl')
    boundary = write(tmp_path / 'pipeline-2026-09-07.jsonl')
    invalid = write(tmp_path / 'pipeline-2026-99-99.jsonl')
    other = write(tmp_path / 'pipeline-important.jsonl')
    backup = write(tmp_path / 'backup/pipeline-2000-01-01.jsonl')
    logger = obs.PipelineLogger(tmp_path, retention_days=30)
    assert logger.expired_paths(now) == [expired]
    logger._purge_expired(now)
    assert all(p.exists() for p in (boundary, invalid, other, backup))


def test_first_daily_write_enforces_retention(tmp_path):
    now = datetime.now().astimezone()
    expired = write(tmp_path / f'pipeline-{(now.date() - timedelta(days=31)).isoformat()}.jsonl')
    logger = obs.PipelineLogger(tmp_path, retention_days=30)
    logger.write(obs.LogRecord(ts=now.isoformat(), run_id='run', query_id='q_1',
                             stage=obs.Stage.GERACAO, status='ok', latencia_ms=0,
                             input='Olá', output='Olá'))
    assert not expired.exists()
    assert logger.log_path(now).is_file()


def test_retention_failure_does_not_fail_query(tmp_path, monkeypatch):
    expired = write(tmp_path / 'pipeline-2000-01-01.jsonl')
    logger = obs.PipelineLogger(tmp_path, retention_days=30)
    unlink = Path.unlink
    def deny_expired(self, *args, **kwargs):
        if self == expired:
            raise PermissionError('locked file')
        return unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', deny_expired)
    now = datetime.now().astimezone()
    logger.write(obs.LogRecord(ts=now.isoformat(), run_id='run', query_id='q_1',
                             stage=obs.Stage.GERACAO, status='ok', latencia_ms=0,
                             input='Olá', output='Olá'))
    assert expired.exists() and logger.log_path(now).exists()


def test_negative_retention_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        obs.PipelineLogger(tmp_path, retention_days=-1)
    with pytest.raises(ValueError):
        maintenance.cleanup_plan(tmp_path, maintenance_days=-1)


def project_test_hooks():
    spec = util.spec_from_file_location('project_test_hooks', Path(__file__).with_name('conftest.py'))
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_managed_test_directory_is_removed_on_success(tmp_path):
    hooks = project_test_hooks()
    settings = SimpleNamespace(option=SimpleNamespace(basetemp=None), rootpath=tmp_path)
    hooks.pytest_configure(settings)
    path = Path(settings.option.basetemp)
    assert path.parent == tmp_path / '.tmp/tests'
    write(path / 'fixture.txt')
    hooks.pytest_sessionfinish(SimpleNamespace(config=settings), 0)
    assert not path.exists()


def test_failed_test_directory_gets_completed_marker(tmp_path):
    hooks = project_test_hooks()
    settings = SimpleNamespace(option=SimpleNamespace(basetemp=None), rootpath=tmp_path)
    hooks.pytest_configure(settings)
    path = Path(settings.option.basetemp)
    write(path / 'fixture.txt')
    hooks.pytest_sessionfinish(SimpleNamespace(config=settings), 1)
    marker = json.loads((path / '.pytest-run.json').read_text(encoding='utf-8'))
    assert marker['completed'] and marker['exitstatus'] == 1
    assert datetime.fromisoformat(marker['finished_at']).tzinfo is not None
    assert (path / 'fixture.txt').exists()


def test_explicit_test_directory_is_not_managed(tmp_path):
    hooks = project_test_hooks()
    settings = SimpleNamespace(option=SimpleNamespace(basetemp=str(tmp_path)), rootpath=tmp_path)
    hooks.pytest_configure(settings)
    hooks.pytest_sessionfinish(SimpleNamespace(config=settings), 0)
    assert settings.option.basetemp == str(tmp_path) and tmp_path.exists()
    assert not hasattr(settings, '_managed_test_temp')


def test_trace_reads_legacy_and_new_logs_without_duplicate_events(tmp_path, monkeypatch):
    logs = tmp_path / 'logs'
    pipeline = logs / 'pipeline'
    monkeypatch.setattr(trace.config, 'LOGS_DIR', logs)
    monkeypatch.setattr(trace.config, 'PIPELINE_LOGS_DIR', pipeline)
    monkeypatch.setattr(obs.default_logger, 'logs_dir', pipeline)
    record = {'query_id': 'q_legacy', 'run_id': 'run', 'session_id': 's_example_session_01',
              'ts': '2026-10-07T10:00:00+00:00', 'stage': 'geracao', 'input': 'Olá', 'output': 'Olá'}
    line = json.dumps(record) + '\n'
    write(logs / 'pipeline-2026-10-07.jsonl', line)
    write(pipeline / 'pipeline-2026-10-07.jsonl', line)
    assert len(trace.montar_trace('q_legacy')['etapas']) == 1
    # An explicitly isolated directory must never fall back to real/legacy logs.
    assert trace.montar_trace('q_legacy', logs_dir=tmp_path / 'isolated') is None
