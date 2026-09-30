"""Verify the curated corpus and stored vectors without calling external services."""
from __future__ import annotations
import collections, hashlib, json, math, sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
BASES = ('contratos_estruturado', 'editais_normativo', 'atas_conversacional', 'pca_agregado')

def rows(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)

def public_url(value):
    parsed = urlparse(str(value or ''))
    return parsed.scheme in ('https', 'http') and bool(parsed.netloc) and parsed.hostname not in ('localhost', '127.0.0.1', '::1')

def main():
    inventory = json.loads((ROOT / 'data/inventory/v6.json').read_text(encoding='utf-8'))
    files = [row for row in inventory['arquivos'] if row['status'] == 'ok']
    counts = collections.Counter()
    spheres = collections.Counter()
    raw_hashes = {}
    for row in files:
        metadata = row.get('document_metadata') or {}
        organization = metadata.get('orgao_cnpj') or metadata.get('orgao_identificador')
        assert organization, ('organization missing', row['source_file'])
        counts[row['base_id'], organization] += 1
        spheres[row['base_id'], metadata.get('esfera')] += 1
        assert public_url(row['origem']), ('non-public origin', row['origem'])
        path = Path(row['source_file'])
        assert not path.is_absolute() and '..' not in path.parts, str(path)
        assert (ROOT / path).resolve().is_relative_to(ROOT.resolve()), str(path)
        digest = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        assert digest == row['sha256'], str(path)
        raw_hashes[row['source_file']] = digest
    assert max(counts.values()) <= 5
    run = json.loads((ROOT / 'eval/runs/v6-pipeline/embedding-run.json').read_text(encoding='utf-8'))
    summary = {}
    for base in BASES:
        chunks = {}
        for row in rows(ROOT / 'data/normalized/v6' / base / 'documents.jsonl'):
            assert row['inventory_version'] == 'v6'
            assert row['chunk_content_sha256'] == hashlib.sha256(row['text'].encode('utf-8')).hexdigest()
            assert public_url(row['origem']), ('chunk non-public origin', row['chunk_id'])
            extras = row.get('extras') or {}
            assert not any(key in extras for key in ('origem', 'source_file', 'arquivo_bruto', 'url_conteudo'))
            for relation in extras.get('relacoes_pncp', []):
                for key, value in (relation.get('document_metadata') or {}).items():
                    assert key not in extras or extras[key] != value, ('duplicate metadata', row['chunk_id'], key)
            if '#derivado:' in row['source_file']:
                source_path=row['source_file'].split('#',1)[0]
                assert source_path == 'data/inventory/v6.json'
                assert extras.get('tipo_origem') == 'calculo_local_sobre_fontes_publicas'
                assert (ROOT / source_path).is_file()
            else:
                assert row['source_file'] in raw_hashes, ('unaccounted source', row['source_file'])
            chunks[row['chunk_id']] = row
        embedded = set()
        configs = set()
        for row in rows(ROOT / run['folder'] / base / 'embeddings.jsonl'):
            assert row['chunk_id'] in chunks
            chunk = chunks[row['chunk_id']]
            for key in ('inventory_version', 'inventory_hash', 'source_file', 'origem', 'chunk_content_sha256'):
                assert row[key] == chunk[key], ('stale embedding metadata', base, key)
            vector = row['embedding']
            assert len(vector) == 1024 and all(math.isfinite(float(v)) for v in vector)
            assert sum(float(v) ** 2 for v in vector) > 0
            embedded.add(row['chunk_id'])
            configs.add(json.dumps(row['encoding_config'], sort_keys=True))
        assert embedded == set(chunks), ('incomplete embeddings', base)
        summary[base] = {'files': sum(value for (dataset, _), value in counts.items() if dataset == base), 'chunks': len(chunks), 'vectors': len(embedded), 'encoding_configurations': len(configs)}
    manifest = json.loads((ROOT / 'index/manifest.json').read_text(encoding='utf-8'))
    assert 'v6' in manifest['source_file']
    assert manifest['source_sha256'] == hashlib.sha256((ROOT / manifest['source_file']).read_bytes()).hexdigest()
    report = {'version': 'v6', 'files': len(files), 'unique_file_hashes': len(set(raw_hashes.values())), 'max_files_per_organization_per_base': max(counts.values()), 'distribution': [{'base': base, 'sphere': sphere, 'files': n} for (base, sphere), n in sorted(spheres.items())], 'bases': summary, 'embedding_folder': run['folder'], 'public_origin_urls': True, 'source_paths_relative_internal': True, 'duplicate_source_aliases': False, 'checks_passed': True}
    destination = ROOT / 'eval/runs/v6-pipeline/offline-audit.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))

if __name__ == '__main__':
    main()
