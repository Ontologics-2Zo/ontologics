"""Exact-version PG/Neo4j reads, with a real isolated graph namespace."""
from dataclasses import replace
import unittest
from uuid import uuid4

from ontologics.graph_views import GraphSettings
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.material_request_registration import read_request
from ontologics.service import Service, DomainError
from examples.order_fulfillment.b_material_request_registration import ACTORS
from tests.order_fulfillment import test_material_request_registration as registration_tests


class ReadbackTests(unittest.TestCase):
    setUp=registration_tests.RegistrationTests.setUp
    session=registration_tests.RegistrationTests.session
    prepare=registration_tests.RegistrationTests.prepare
    approve=registration_tests.RegistrationTests.approve

    def test_projection_pending_then_exact_common_read_without_receipt_mutation(self):
        settings=replace(GraphSettings.from_environment(backend='neo4j'),namespace='test_request_'+uuid4().hex)
        provider=DevelopmentIdentityProvider(ACTORS,enabled=True)
        graph_host=CommonHost(provider,self.database,graph_settings=settings)
        created=self.prepare();version=self.approve(created)
        before=self.service.db.execute('SELECT nodes FROM snapshots WHERE version=%s',(version,)).fetchone()['nodes']
        with graph_host.request(DevelopmentContext(ACTORS[2].subject)) as session:
            with self.assertRaises(DomainError) as error: read_request(session,created['evidence_id'],version=version)
            self.assertEqual('GRAPH_NOT_READY',error.exception.code)
        actor='system:graph-projector'
        projector=Service(self.database,principals={actor:{'kind':'system','roles':['read','index']}},graph_settings=settings)
        self.addCleanup(projector.close)
        namespace=projector.graph_views.namespace
        def clean():
            with projector.graph_views.projection()._driver.session(database=settings.database) as session:
                session.run('MATCH (n) WHERE n.namespace=$namespace DETACH DELETE n',namespace=namespace).consume()
        self.addCleanup(clean)
        result=projector.graph_views.synchronize(actor,version,request_key='sync')
        self.assertTrue(result['ok'],result)
        with self.session(2) as session:
            pg=read_request(session,created['evidence_id'],version=version)
        with graph_host.request(DevelopmentContext(ACTORS[2].subject)) as session:
            graph=read_request(session,created['evidence_id'],version=version)
            self.assertEqual(pg,graph)
            with self.assertRaises(ValueError): read_request(session,created['evidence_id'],version=None)
            with self.assertRaises(DomainError): read_request(session,created['evidence_id'],version=version+100)
        after=self.service.db.execute('SELECT nodes FROM snapshots WHERE version=%s',(version,)).fetchone()['nodes']
        self.assertEqual(before,after)
        self.assertFalse(graph['a_received'])
        no_read=UserIdentity('no-reader',frozenset())
        restricted=CommonHost(DevelopmentIdentityProvider([no_read],enabled=True),self.database,graph_settings=settings)
        with restricted.request(DevelopmentContext(no_read.subject)) as session:
            with self.assertRaises(DomainError) as error: read_request(session,created['evidence_id'],version=version)
            self.assertEqual('FORBIDDEN',error.exception.code)
