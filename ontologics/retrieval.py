"""Versioned pgvector evidence candidates, independent of exact CQ decisions.

Host supplies embeddings from one chosen model. This class never generates facts,
approves a draft, or treats similarity as component identity or temporal proof.
"""
from __future__ import annotations
import math
from .model import canonical, digest
from .service import DomainError


class EvidenceIndex:
    def __init__(self, service, actor, *, model, dimensions):
        if not isinstance(model, str) or not 1 <= len(model.strip()) <= 200:
            raise ValueError('Use a nonempty embedding model identifier')
        if type(dimensions) is not int or not 1 <= dimensions <= 2000:
            raise ValueError('Embedding dimensions must be 1..2000')
        self.service, self.actor, self.model, self.dimensions = service, actor, model, dimensions

    def _vector(self, vector):
        if not isinstance(vector, list) or len(vector) != self.dimensions or any(
                type(x) not in (float, int) or abs(x) > 1e30 or not math.isfinite(x) for x in vector) or not any(vector):
            raise DomainError('INVALID_EMBEDDING', 'Use a finite nonzero vector with the configured dimensions')
        return canonical(vector)

    @staticmethod
    def _document(nodes, evidence_id):
        idx = {n['id']: n for n in nodes}
        evidence = idx.get(evidence_id)
        if not evidence or evidence['type'] != 'Evidence':
            raise DomainError('EVIDENCE_NOT_FOUND', 'Index only evidence in this confirmed snapshot')
        source = idx[evidence['source_id']]
        facts = sorted((n for n in nodes if evidence_id in n.get('evidence', [])), key=lambda n: n['id'])
        return {'evidence': evidence, 'source': source, 'facts': facts}

    def documents(self, evidence_ids, *, version=None):
        """Texts to embed outside a DB transaction; returned version is mandatory at index time."""
        def act():
            v = self.service._version() if version is None else version
            nodes = self.service._nodes(v)
            if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 200 or any(not isinstance(x,str) for x in evidence_ids):
                raise DomainError('INVALID_INPUT', 'Select 1..200 evidence IDs')
            docs = []
            for eid in evidence_ids:
                document = self._document(nodes, eid)
                docs.append({'evidence_id': eid, 'evidence': [eid], 'text': canonical(document),
                             'content_hash': digest(document)})
            return self.service._envelope(ok=True, data={'documents': docs}, version=v)
        return self.service._run(self.actor, 'evidence_documents', {'ids': evidence_ids, 'version': version}, act)

    def index(self, entries, *, version, request_key):
        """Host index role only. Immutable derived cache; confirmed graph is unchanged."""
        def act():
            nodes = self.service._nodes(version)
            if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
                raise DomainError('INVALID_INPUT', 'Index 1..200 evidence vectors per batch')
            ids = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {'evidence_id', 'content_hash', 'embedding'} or not isinstance(entry['evidence_id'], str):
                    raise DomainError('INVALID_INPUT', 'Provide evidence_id, content_hash and embedding')
                eid = entry['evidence_id']
                if eid in ids: raise DomainError('INVALID_INPUT', 'Duplicate evidence in batch')
                document = self._document(nodes, eid)
                if entry['content_hash'] != digest(document):
                    raise DomainError('EVIDENCE_CONTENT_MISMATCH', 'Embed the exact document for this data version')
                vector = self._vector(entry['embedding'])
                existing = self.service.db.execute('''SELECT content_hash, dimensions, embedding = %s::public.vector AS same
                    FROM evidence_vectors WHERE data_version=%s AND evidence_id=%s AND model=%s''',
                    (vector, version, eid, self.model)).fetchone()
                if existing:
                    if existing['content_hash'] != entry['content_hash'] or existing['dimensions'] != self.dimensions or not existing['same']:
                        raise DomainError('INDEX_CONFLICT', 'Existing model/version evidence embedding is immutable')
                else:
                    self.service.db.execute('''INSERT INTO evidence_vectors
                        (data_version,evidence_id,model,dimensions,content_hash,content,embedding)
                        VALUES(%s,%s,%s,%s,%s,%s,%s::public.vector)''',
                        (version,eid,self.model,self.dimensions,entry['content_hash'],canonical(document),vector))
                ids.append(eid)
            return self.service._envelope(ok=True, data={'indexed': len(ids), 'evidence': ids,
                'index_data_version': version, 'model': self.model, 'dimensions': self.dimensions,
                'scope': 'derived retrieval index; no confirmed graph changes'})
        return self.service._run(self.actor, 'index_evidence', {'entries':entries,'version':version,
            'model':self.model,'dimensions':self.dimensions}, act, permission='index', request_key=request_key)

    def search(self, embedding, *, version=None, origin=None, limit=5):
        def act():
            vector = self._vector(embedding)
            if origin not in (None, 'real', 'synthetic') or type(limit) is not int or not 1 <= limit <= 20:
                raise DomainError('INVALID_INPUT', 'Use origin real/synthetic and limit 1..20')
            v = self.service._version() if version is None else version
            nodes = self.service._nodes(v)
            # Exact scan is intentional for the small baseline. Filter before ranking.
            rows = self.service.db.execute('''SELECT evidence_id, content_hash,
                1 - (embedding <=> %s::public.vector) AS similarity FROM evidence_vectors
                WHERE data_version=%s AND model=%s AND dimensions=%s
                AND (%s::text IS NULL OR content::jsonb->'evidence'->>'origin'=%s)
                ORDER BY embedding <=> %s::public.vector, evidence_id LIMIT %s''',
                (vector,v,self.model,self.dimensions,origin,origin,vector,limit)).fetchall()
            candidates = []
            for row in rows:
                d = self._document(nodes,row['evidence_id'])
                if digest(d) != row['content_hash']:
                    raise DomainError('INDEX_INTEGRITY', 'Indexed content no longer matches source snapshot')
                candidates.append({'evidence': [row['evidence_id']], 'similarity': row['similarity'],
                    'origin': d['evidence']['origin'], 'locator': d['evidence']['locator'],
                    'source': d['source'], 'facts': d['facts'], 'content_hash': row['content_hash']})
            return self.service._envelope(ok=True,data={'candidates':candidates,'model':self.model,
                'dimensions':self.dimensions,'candidate_only':True,'index_scope':'indexed evidence only; not exhaustive knowledge'},version=v,
                warnings=[{'code':'SIMILARITY_NOT_A_FACT','detail':'Use exact ID/time/review tools for decisions'}])
        return self.service._run(self.actor,'search_evidence',{'embedding':embedding,'version':version,
            'model':self.model,'dimensions':self.dimensions,'origin':origin,'limit':limit},act)
