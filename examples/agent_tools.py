"""Run from repo root: python3 -m examples.agent_tools (no model/SDK required)."""
import json
import uuid
from ontologics.demo import PRINCIPALS, CONFIG_L, example_queries, fixture_nodes, must
from ontologics.ingest import build_catalog
from ontologics.service import AgentGateway, Service


def main():
    service = Service(schema='tools_' + uuid.uuid4().hex, principals=PRINCIPALS)
    try:
        operations, supplement = fixture_nodes()
        must(service.bootstrap('seed-admin', build_catalog()['nodes'] + operations, request_key='example-seed'))
        agent = AgentGateway(service, 'product-agent')
        config = must(agent.call('configuration', {'configuration_id': CONFIG_L}))
        result = must(agent.call('investigate', {'query': example_queries()['A']}))
        proposal = must(agent.call('prepare_draft', {'operations': supplement, 'request_key': 'example-supplement'}))
        d = proposal['data']
        preview = must(agent.call('preview_investigation', {'draft_id': d['draft_id'], 'version': d['draft_version'], 'query': example_queries()['A']}))
        print(json.dumps({'bom_items': len(config['data']['items']),
            'current': result['data']['results'], 'draft_status': d['status'],
            'proposed_U104': preview['data']['result']['results'][0],
            'preview_warning': preview['warnings'], 'next': 'Human review through trusted host; Agent stops here.'}, ensure_ascii=False, indent=2))
    finally:
        service.close()

if __name__ == '__main__': main()
