"""A-only PostgreSQL input bundle for the local development web workflow."""
from ..model import canonical
from ..service import DomainError


def initialize_inputs(service, payload):
    """Trusted startup import into a dedicated test schema; never an HTTP write."""
    if not service.schema.startswith('test_'):
        raise ValueError('Web input import requires a dedicated test schema')
    with service.db.transaction():
        service.db.execute('''CREATE TABLE IF NOT EXISTS pp_web_inputs(
            id INTEGER PRIMARY KEY CHECK(id=1), payload JSONB NOT NULL)''')
        service.db.execute('INSERT INTO pp_web_inputs VALUES(1,%s)', (canonical(payload),))


class WebInputStore:
    def __init__(self, host):
        self.host = host

    def get(self, context):
        with self.host.request(context) as session:
            service = session._service
            def read():
                row = service.db.execute('SELECT payload FROM pp_web_inputs WHERE id=1').fetchone()
                if row is None:
                    raise DomainError('INPUTS_NOT_FOUND', 'PostgreSQL에 계약·재고·생산 기준 자료가 없습니다.')
                return service._envelope(ok=True, data=row['payload'])
            return service._run(session.context['actor'], 'planning_web_inputs', {}, read,
                                permission='read')
