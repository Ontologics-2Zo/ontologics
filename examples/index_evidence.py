"""Index a confirmed snapshot with one embedding API; never approve or alter facts.

No schema is deleted. Existing model/version/content embeddings are kept. Failed
or oversized documents remain explicitly unindexed; rerunning only embeds missing
entries. The index role is configured here in trusted local code, never granted
to an Agent or browser user. Reports contain IDs/counts/status, not source text.
"""
from __future__ import annotations

import argparse
import json

from ontologics.database import DatabaseError, DatabaseSettings, environment_values, in_transaction
from ontologics.embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, MAX_BATCH_SIZE, MAX_TEXT_BYTES, OpenAIEmbeddings
from ontologics.model import digest
from ontologics.openai_provider import OpenAISettings, ProviderError
from ontologics.retrieval import EvidenceIndex
from ontologics.service import DomainError, Service


INDEX_ACTOR = 'system:evidence-indexer'


def _must(result):
    if not result['ok']:
        issue = result['errors'][0]
        raise DomainError(issue['code'], 'Evidence indexing common tool failed')
    return result


def index_confirmed_evidence(database, embedder, *, version, evidence_ids=None):
    if type(version) is not int or version < 1:
        raise DomainError('INVALID_INPUT', 'Select a positive confirmed data version')
    if (embedder.model, embedder.dimensions) != (EMBEDDING_MODEL, EMBEDDING_DIMENSIONS):
        raise DomainError('INVALID_INPUT', 'Use the fixed embedding model and dimension')
    if evidence_ids is not None and (not isinstance(evidence_ids, list) or not evidence_ids or
            any(not isinstance(e, str) for e in evidence_ids) or len(set(evidence_ids)) != len(evidence_ids)):
        raise DomainError('INVALID_INPUT', 'Select distinct evidence IDs or omit the filter')
    service = Service(database, principals={INDEX_ACTOR: {'kind': 'system', 'roles': ['read', 'index']}})
    report = {'status': 'running', 'schema': service.schema, 'data_version': version,
              'model': embedder.model, 'dimensions': embedder.dimensions,
              'selection': 'all_evidence_in_snapshot' if evidence_ids is None else 'explicit_evidence_subset',
              'selected_count': 0, 'indexed_ids': [], 'already_indexed_ids': [], 'unindexed': [],
              'scope': 'derived retrieval candidates only; no confirmed graph or approval changes'}
    try:
        all_evidence = _must(service.lookup(INDEX_ACTOR, kind='Evidence', version=version))['data']
        all_ids = {n['id'] for n in all_evidence}
        selected = sorted(all_ids if evidence_ids is None else evidence_ids)
        if not set(selected) <= all_ids:
            raise DomainError('EVIDENCE_NOT_FOUND', 'Selected evidence is absent from this confirmed snapshot')
        report['selected_count'] = len(selected)
        report['snapshot_evidence_count'] = len(all_ids)
        index = EvidenceIndex(service, INDEX_ACTOR, model=embedder.model, dimensions=embedder.dimensions)
        # Verify whole-snapshot cache coverage independently of this run's filter.
        # Only selected documents are sent to the embedding provider.
        snapshot_ids = sorted(all_ids)
        snapshot_docs = []
        for offset in range(0, len(snapshot_ids), 200):
            snapshot_docs.extend(_must(index.documents(snapshot_ids[offset:offset+200], version=version))['data']['documents'])
        selected_ids = set(selected)
        docs = [doc for doc in snapshot_docs if doc['evidence_id'] in selected_ids]

        def cached():
            return {r['evidence_id']: r for r in service.db.execute(
                'SELECT evidence_id,content_hash,dimensions FROM evidence_vectors WHERE data_version=%s AND model=%s',
                (version, embedder.model))}

        existing = cached()
        pending = []
        for doc in docs:
            row = existing.get(doc['evidence_id'])
            if row:
                if (row['content_hash'], row['dimensions']) == (doc['content_hash'], embedder.dimensions):
                    report['already_indexed_ids'].append(doc['evidence_id'])
                else:
                    report['unindexed'].append({'evidence_id': doc['evidence_id'], 'code': 'INDEX_CONFLICT'})
            elif len(doc['text'].encode('utf-8')) > MAX_TEXT_BYTES:
                report['unindexed'].append({'evidence_id': doc['evidence_id'], 'code': 'EMBEDDING_INPUT_TOO_LARGE'})
            else:
                pending.append(doc)
        for offset in range(0, len(pending), MAX_BATCH_SIZE):
            batch = pending[offset:offset+MAX_BATCH_SIZE]
            try:
                if in_transaction(service.db):
                    raise DomainError('TRANSACTION_BOUNDARY', 'Embedding API must run outside a DB transaction')
                vectors = embedder.embed([doc['text'] for doc in batch])
                if not isinstance(vectors, list) or len(vectors) != len(batch):
                    raise DomainError('OPENAI_EMBEDDING_RESPONSE_INVALID', 'Incomplete embedding batch')
                entries = [{'evidence_id': doc['evidence_id'], 'content_hash': doc['content_hash'], 'embedding': vector}
                           for doc, vector in zip(batch, vectors)]
                # A competing process may have completed these documents during
                # the network call. Keep the first matching cache, never replace it.
                existing = cached()
                to_index = []
                for entry in entries:
                    row = existing.get(entry['evidence_id'])
                    if row and (row['content_hash'], row['dimensions']) == (entry['content_hash'], embedder.dimensions):
                        report['already_indexed_ids'].append(entry['evidence_id'])
                    elif row:
                        report['unindexed'].append({'evidence_id': entry['evidence_id'], 'code': 'INDEX_CONFLICT'})
                    else:
                        to_index.append(entry)
                if not to_index:
                    continue
                key = 'embedding-index:' + digest({'version': version, 'model': embedder.model, 'entries': to_index})
                outcome = index.index(to_index, version=version, request_key=key)
                if outcome['ok']:
                    report['indexed_ids'].extend(entry['evidence_id'] for entry in to_index)
                else:
                    # On a race, the immutable matching cache is still usable.
                    # Missing entries stay explicit and can be retried later.
                    existing = cached()
                    code = outcome['errors'][0]['code']
                    for entry in to_index:
                        row = existing.get(entry['evidence_id'])
                        if row and (row['content_hash'], row['dimensions']) == (entry['content_hash'], embedder.dimensions):
                            report['already_indexed_ids'].append(entry['evidence_id'])
                        else:
                            report['unindexed'].append({'evidence_id': entry['evidence_id'], 'code': code})
            except (DomainError, ProviderError) as exc:
                known = set(report['indexed_ids'] + report['already_indexed_ids']) | {r['evidence_id'] for r in report['unindexed']}
                report['unindexed'].extend({'evidence_id': doc['evidence_id'], 'code': exc.code}
                                          for doc in batch if doc['evidence_id'] not in known)
                # Don't spend API calls on remaining batches after configuration
                # or transport failure. A later invocation resumes missing IDs.
                untouched = pending[offset+MAX_BATCH_SIZE:]
                report['unindexed'].extend({'evidence_id': doc['evidence_id'], 'code': 'NOT_ATTEMPTED_AFTER_FAILURE'} for doc in untouched)
                break
        report['indexed_ids'] = sorted(set(report['indexed_ids']))
        report['already_indexed_ids'] = sorted(set(report['already_indexed_ids']))
        report['indexed_count'] = len(report['indexed_ids'])
        report['already_indexed_count'] = len(report['already_indexed_ids'])
        report['unindexed_count'] = len(report['unindexed'])
        report['selection_complete'] = report['unindexed_count'] == 0
        final_cache = cached()
        valid_snapshot_ids = {doc['evidence_id'] for doc in snapshot_docs
                              if (row := final_cache.get(doc['evidence_id'])) is not None
                              and (row['content_hash'], row['dimensions']) ==
                                  (doc['content_hash'], embedder.dimensions)}
        report['snapshot_complete'] = valid_snapshot_ids == all_ids
        report['status'] = 'completed' if report['selection_complete'] else 'partial'
        report['provider_calls'] = list(getattr(embedder, 'calls', []))
        return report
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--version', type=int, required=True)
    parser.add_argument('--env-file', help='Local database settings file')
    parser.add_argument('--model-env-file', help='Local OpenAI settings file; default .env/.env.local and process environment')
    parser.add_argument('--evidence-id', action='append', dest='evidence_ids')
    args = parser.parse_args()
    try:
        database = DatabaseSettings.from_environment(schema=args.schema, env_file=args.env_file)
        settings = OpenAISettings.from_environment(environ=environment_values(args.model_env_file))
        result = index_confirmed_evidence(database, OpenAIEmbeddings(settings), version=args.version, evidence_ids=args.evidence_ids)
    except (DomainError, ProviderError, DatabaseError) as exc:
        result = {'status': 'failed', 'error': {'code': getattr(exc, 'code', 'STORAGE_ERROR'),
                  'message': 'Index preparation failed; inspect local settings and the selected confirmed version.'}}
    except Exception:
        result = {'status': 'failed', 'error': {'code': 'INDEX_RUN_FAILED',
                  'message': 'Index run failed; rerun to fill only missing evidence after resolving the cause.'}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'completed' else 1)


if __name__ == '__main__':
    main()
