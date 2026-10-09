"""Recover v6 garbled PDF pages with the existing extraction and embedding CLIs.

No collection, remote model calls, database rebuilds or uploads. Originals are
preserved under data/backups/recovery/. Diagnostics go under logs/maintenance/.
A staged index is validated before replacing index/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import config
from src.index import build_index, stored_embed_fn, file_sha256
from src.qualidade_corpus import motivo_de_texto_ilegivel

BASES = ('contratos_estruturado', 'editais_normativo', 'atas_conversacional', 'pca_agregado')
AFFECTED = ('atas_conversacional', 'editais_normativo')
OLD_FOLDER = ROOT / 'data/embeddings/2026-09-30_12-08-51'


def rows(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-normalized', type=Path, default=ROOT / 'data/normalized',
                        help='Original normalized snapshot for before/after counts when retrying')
    args = parser.parse_args()
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    run = config.MAINTENANCE_LOGS_DIR / 'recovery' / stamp
    backup = config.BACKUPS_DIR / 'recovery' / stamp
    run.mkdir(parents=True)
    backup.mkdir(parents=True)
    state = {'started_at': stamp, 'run': run.relative_to(ROOT).as_posix(),
             'backup': backup.relative_to(ROOT).as_posix()}

    def stage(name, **extra):
        state.update(stage=name, **extra)
        write_json(run / 'state.json', state)
        print(name, flush=True)

    def command(args, name):
        with (run / (name + '.log')).open('w', encoding='utf-8') as output:
            environment = {**os.environ, 'PYTHONUTF8': '1'}
            if args[:2] == ['-m', 'src.run_local_embeddings']:
                # The parent's dotenv load may export an empty custom key;
                # OpenAI's local client still requires a nonempty placeholder.
                environment['CUSTOM_API_KEY'] = 'local'
            result = subprocess.run([sys.executable, *args], cwd=ROOT, stdout=output,
                                    stderr=subprocess.STDOUT, env=environment)
        if result.returncode:
            raise RuntimeError(f'{name} failed: see {run / (name + ".log")}')

    try:
        stage('backup')
        # Only active files can be changed by this recovery. Do not copy old
        # versions or archival snapshots on every retry.
        normalized_backup = backup / 'normalized-before'
        normalized_backup.mkdir()
        shutil.copytree(ROOT / 'data/normalized/v6', normalized_backup / 'v6')
        shutil.copy2(ROOT / 'data/normalized/documents.jsonl', normalized_backup / 'documents.jsonl')
        if (ROOT / 'data/normalized/emails_sinteticos').exists():
            shutil.copytree(ROOT / 'data/normalized/emails_sinteticos', normalized_backup / 'emails_sinteticos')
        shutil.copy2(ROOT / 'data/inventory/qualidade_v6.json', backup / 'qualidade-before.json')
        for name in ('embedding-run.json', 'offline-audit.json'):
            shutil.copy2(ROOT / 'eval/runs/v6-pipeline' / name, backup / name)
        before = {base: rows(args.baseline_normalized / 'v6' / base / 'documents.jsonl') for base in BASES}
        original_root = rows(ROOT / 'data/normalized/documents.jsonl')
        old_index = json.loads((ROOT / 'index/manifest.json').read_text(encoding='utf-8'))
        old_config = old_index['bases'][0]['encoding_config']
        assert file_sha256(Path(old_config['checkpoint'])) == old_config['checkpoint_sha256']

        # The current manifest includes later changes (e.g. synthetic emails).
        # Use the byte-identical v6 collection manifest, including its CRLF,
        # rather than bypassing provenance validation or recollecting sources.
        manifest_commit = 'bb5aa49931219040f53223567b86a23ec8eff0c1'
        manifest_bytes = subprocess.check_output(['git', 'show', manifest_commit + ':corpus_manifest.yaml'])
        manifest_bytes = manifest_bytes.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        inventory = json.loads((ROOT / 'data/inventory/v6.json').read_text(encoding='utf-8'))
        assert 'sha256:' + hashlib.sha256(manifest_bytes).hexdigest() == inventory['manifest_hash']
        manifest_path = backup / 'corpus_manifest_v6.yaml'
        manifest_path.write_bytes(manifest_bytes)
        state['collection_manifest_commit'] = manifest_commit
        state['collection_manifest_sha256'] = inventory['manifest_hash']

        for base in AFFECTED:
            stage('prepare-' + base)
            command(['-m', 'src.prepare_documents', '--latest', '--base-id', base,
                     '--manifest', str(manifest_path)], 'prepare-' + base)
            report = json.loads((ROOT / 'data/normalized/v6' / base / 'extraction_report.json').read_text(encoding='utf-8'))
            assert not report['resumo'].get('falhas'), report['resumo']

        stage('synchronize-and-quality')
        combined = ROOT / 'data/normalized/v6/documents.jsonl'
        with combined.open('wb') as output:
            for base in BASES:
                with (ROOT / 'data/normalized/v6' / base / 'documents.jsonl').open('rb') as source:
                    shutil.copyfileobj(source, output)
        active = ROOT / 'data/normalized/documents.jsonl'
        with active.open('wb') as output:
            output.write(combined.read_bytes())
            for row in original_root:
                if row['base_id'] not in BASES:
                    output.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8'))
        command(['-m', 'src.qualidade_corpus'], 'quality')
        quality = json.loads((ROOT / 'data/inventory/qualidade_v6.json').read_text(encoding='utf-8'))
        assert not quality['documentos_com_texto_ilegivel'], quality['documentos_com_texto_ilegivel']

        folder = ROOT / 'data/embeddings' / (stamp + '_ocr-recovery')
        state['embedding_folder'] = folder.relative_to(ROOT).as_posix()
        summary = {}
        for base in BASES:
            source = ROOT / 'data/normalized/v6' / base / 'documents.jsonl'
            current = rows(source)
            assert all(not motivo_de_texto_ilegivel(row['text']) for row in current)
            assert all(hashlib.sha256(row['text'].encode('utf-8')).hexdigest() == row['chunk_content_sha256'] for row in current)
            destination = folder / base / 'embeddings.jsonl'
            destination.parent.mkdir(parents=True)
            shutil.copy2(OLD_FOLDER / base / 'embeddings.jsonl', destination)
            if base in AFFECTED:
                stage('embeddings-' + base)
                command(['-m', 'src.run_local_embeddings', '--run', '--input', str(source),
                         '--output', str(destination), '--threads', '2', '--gpu-layers', 'all',
                         '--parallel', '1', '--ubatch-size', '2048', '--context-size', '4096',
                         '--batch-size', '16', '--pause-seconds', '0'], 'embeddings-' + base)
            embedded = rows(destination)
            by_id = {row['chunk_id']: row for row in current}
            assert {row['chunk_id'] for row in embedded} == set(by_id)
            for row in embedded:
                chunk = by_id[row['chunk_id']]
                assert row['encoding_config'] == old_config
                for key in ('inventory_version', 'inventory_hash', 'source_file', 'origem', 'chunk_content_sha256'):
                    assert row[key] == chunk[key], (base, key, row['chunk_id'])
                assert len(row['embedding']) == 1024
                assert all(math.isfinite(value) for value in row['embedding']) and any(row['embedding'])
            # Text-identical chunks must retain exactly the same vectors.
            old_vectors = {row['chunk_content_sha256']: row['embedding'] for row in rows(OLD_FOLDER / base / 'embeddings.jsonl')}
            reused = sum(row['chunk_content_sha256'] in old_vectors for row in embedded)
            assert all(row['embedding'] == old_vectors[row['chunk_content_sha256']]
                       for row in embedded if row['chunk_content_sha256'] in old_vectors)
            generated = 0
            recovered_pages = 0
            if base in AFFECTED:
                embedding_report = json.loads(destination.with_name('embedding_report.json').read_text(encoding='utf-8'))
                generated = embedding_report['gerados']
                old_units = {u['document_id']: u for u in rows(args.baseline_normalized / 'v6' / base / 'documents.units.jsonl')}
                new_units = {u['document_id']: u for u in rows(source.with_name('documents.units.jsonl'))}
                assert set(old_units) == set(new_units), 'Recovery must not lose source pages'
                assert all(old_units[k]['data_vigencia'] == new_units[k]['data_vigencia'] for k in old_units)
                recovered_pages = sum(old_units[k]['text'] != new_units[k]['text'] for k in old_units)
            summary[base] = {'before_chunks': len(before[base]), 'chunks': len(current),
                             'vectors': len(embedded), 'reused_vectors': reused,
                             'new_vectors': len(embedded) - reused,
                             'generated_unique_texts': generated, 'recovered_pages': recovered_pages}

        stage('build-staged-index')
        staged = run / 'index-staging'
        shutil.copytree(ROOT / 'index', staged)
        for base in AFFECTED:
            embed, model = stored_embed_fn(folder / base / 'embeddings.jsonl')
            build_index(ROOT / 'data/normalized/v6' / base / 'documents.jsonl', staged,
                        embed, model=model, merge_existing=True, batch_size=256)
        manifest = json.loads((staged / 'manifest.json').read_text(encoding='utf-8'))
        manifest['source_file'] = combined.relative_to(ROOT).as_posix()
        manifest['source_sha256'] = file_sha256(combined)
        write_json(staged / 'manifest.json', manifest)
        import faiss
        for base in manifest['bases']:
            path = staged / base['base_id']
            assert faiss.read_index(str(path / 'vectors.faiss')).ntotal == base['total_chunks']
            vectors = rows(path / 'vectors.jsonl')
            assert len(vectors) == base['total_chunks']
            if base['base_id'] in AFFECTED:
                assert len(vectors) == summary[base['base_id']]['chunks']
            else:
                for prior in (ROOT / 'index' / base['base_id']).iterdir():
                    if prior.is_file():
                        assert file_sha256(prior) == file_sha256(path / prior.name)
        assert manifest['chunks_ilegiveis_ignorados'] == 0

        stage('promote-index')
        # The existing index stays live until promotion. Move it once into
        # the backup instead of retaining two byte-identical index copies.
        displaced = backup / 'index-before'
        (ROOT / 'index').rename(displaced)
        try:
            staged.rename(ROOT / 'index')
        except OSError:
            displaced.rename(ROOT / 'index')
            raise
        stage('offline-audit')
        run_pointer_path = ROOT / 'eval/runs/v6-pipeline/embedding-run.json'
        run_pointer = json.loads(run_pointer_path.read_text(encoding='utf-8'))
        run_pointer['previous_folder'] = run_pointer['folder']
        run_pointer['folder'] = folder.relative_to(ROOT).as_posix()
        run_pointer['recovery_report'] = 'eval/runs/v6-ocr-recovery.json'
        write_json(run_pointer_path, run_pointer)
        command(['eval/runs/audit_v6.py'], 'offline-audit')
        state.update(bases=summary, total_v6_chunks=sum(v['chunks'] for v in summary.values()),
                     total_index_chunks=sum(v['total_chunks'] for v in manifest['bases']),
                     generated_unique_texts=sum(v['generated_unique_texts'] for v in summary.values()),
                     unreadable_chunks=0, checks_passed=True,
                     database_rebuild_performed=False, uploads_performed=False)
        stage('completed')
        report = ROOT / 'eval/runs/v6-ocr-recovery.json'
        write_json(report, state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    except Exception as error:
        stage('failed', error=f'{type(error).__name__}: {error}')
        raise


if __name__ == '__main__':
    main()
