"""Contract candidates are grounded proposals, never approvals or calculations."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

if importlib.util.find_spec('ontologics.production_planning.contract_intake'):
    from ontologics.production_planning import contract_intake as intake
else:
    intake = None
ROOT = Path(__file__).resolve().parents[2]
TEXT = ('Item: item:demo:1\nModel: ESP32-POE\nRevision: L\nVariant: WROOM\n'
        'Order: 100 piece\nDue: 2026-11-10\nFulfilled: 0\nAllocated: 0\nPlanned: 100\n')


def claim(e, quote, value=None, *, occurrence=0):
    segment = [s for s in e['segments'] if quote in s['text']][occurrence]
    start = segment['text'].index(quote)
    return dict(value=quote if value is None else value, evidence=[dict(
        segment_id=segment['id'], start=start, end=start+len(quote), quote=quote)])


def interpretation(e):
    fields = {k: [claim(e,q,v)] for k,q,v in [
        ('contract_item_id','item:demo:1',None),('product_model','ESP32-POE',None),
        ('revision','L',None),('variant','WROOM',None),('ordered_quantity','100',100),
        ('unit','piece',None),('delivery_due',next(s['text'].split('Due: ',1)[1] for s in e['segments'] if s['text'].startswith('Due: ')),None),
        ('fulfilled_quantity','0',0),('allocated_quantity','0',0),('planned_input_quantity','100',100)]}
    for key, prefix, quote, number in [('fulfilled_quantity','Fulfilled:','0',0), ('allocated_quantity','Allocated:','0',0), ('planned_input_quantity','Planned:','100',100)]:
        segment=next(s for s in e['segments'] if s['text'].startswith(prefix))
        start=segment['text'].rindex(quote)
        fields[key]=[dict(value=number,evidence=[dict(segment_id=segment['id'],start=start,end=start+len(quote),quote=quote)])]
    return dict(source_sha256=e['source']['sha256'], data_version=1, items=[dict(fields=fields)])


def catalog_bundle():
    nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
    idx={n['id']:n for n in nodes}; candidates=[]
    for c in nodes:
        if c['type']!='Configuration':continue
        r=idx[c['revision_id']]
        items=[n for n in nodes if n['type']=='BOMItem' and n['configuration_id']==c['id']]
        candidates.append(dict(configuration=c,revision=r,product=idx[r['model_id']],
            items=items,components=[idx[i] for i in sorted({n['component_id'] for n in items})]))
    return dict(data_version=1,candidates=candidates,provenance='local_catalog_fixture')


class IntakeFixture:
    def setUp(self):
        self.assertIsNotNone(intake,'Task 02 intake is not implemented')
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'contract.txt';self.path.write_bytes(TEXT.encode())
        self.extracted=intake.extract_contract_text(self.path)
        self.interpreted=interpretation(self.extracted);self.bundle=catalog_bundle()

    def prepare(self,**kwargs):
        return intake.prepare_contract_demand(self.extracted,self.interpreted,self.bundle,**kwargs)

    def codes(self,r):return {x['code'] for x in r['errors']+r['unresolved']}

    def replace_text(self,text):
        self.path.write_text(text);self.extracted=intake.extract_contract_text(self.path)
        self.interpreted=interpretation(self.extracted)


class ContractIntakeTests(IntakeFixture,unittest.TestCase):
    def test_extract_bytes_hash_unicode_crlf_and_positions(self):
        raw='한글 계약\r\n\r\n수량 100\n'.encode();self.path.write_bytes(raw)
        e=intake.extract_contract_text(self.path)
        self.assertEqual(raw,e['source_bytes']);self.assertEqual(raw,self.path.read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(),e['source']['sha256'])
        s=e['segments'][2]
        self.assertEqual(('수량 100',3,2),(s['text'],s['line'],s['paragraph']))
        self.assertEqual(s['text'],e['text'][s['start']:s['end']])

    def test_normal_candidate_quantity_mapping_date_and_unconfirmed_state(self):
        r=self.prepare();self.assertTrue(r['valid'],r)
        self.assertEqual('needs_confirmation',r['status']);i=r['items'][0]
        self.assertEqual(100,i['contract_item_candidate']['ordered_quantity'])
        self.assertEqual(100,i['common_item_candidate']['quantity'])
        self.assertEqual('config:esp32-poe:L:WROOM',i['contract_item_candidate']['configuration_id'])
        self.assertEqual('date',i['contract_item_candidate']['delivery_due_precision'])
        self.assertEqual('2026-11-10',i['contract_item_candidate']['delivery_due'])
        self.assertFalse(r['confirmation']['verified'])

    def test_model_only_keeps_both_revisions_and_unknown_variant(self):
        for k in ('revision','variant'):del self.interpreted['items'][0]['fields'][k]
        i=self.prepare()['items'][0]
        self.assertEqual(2,len(i['configuration_candidates']))
        self.assertIsNone(i['contract_item_candidate']['configuration_id'])
        self.assertEqual('missing',i['fields']['variant']['state'])

    def test_l_prefix_of_l1_is_rejected(self):
        self.replace_text(TEXT.replace('Revision: L\n','Revision: L1\n'))
        r=self.prepare();self.assertIn('EVIDENCE_VALUE_MISMATCH',self.codes(r))
        self.assertIsNone(r['items'][0]['contract_item_candidate']['configuration_id'])

    def test_exact_l1_selects_only_l1_candidate(self):
        self.replace_text(TEXT.replace('Revision: L\n','Revision: L1\n'))
        self.interpreted['items'][0]['fields']['revision']=[claim(self.extracted,'L1')]
        self.assertEqual('config:esp32-poe:L1:WROOM',self.prepare()['items'][0]['contract_item_candidate']['configuration_id'])

    def test_quantity_bool_float_null_string_and_nonpositive_are_not_coerced(self):
        for bad in (True,False,100.0,None,'100',0,-1,[],{}):
            with self.subTest(bad=bad):
                self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value']=bad
                r=self.prepare();self.assertIn('INVALID_QUANTITY',self.codes(r))
                self.assertIsNone(r['items'][0]['common_item_candidate']['quantity'])

    def test_quantity_span_cannot_hide_sign_decimal_or_grouping(self):
        for written in ('-100','+100','100.5','100,000','1,100'):
            with self.subTest(written=written):
                self.replace_text(TEXT.replace('Order: 100 piece','Order: '+written+' piece'))
                r=self.prepare()
                self.assertIn('EVIDENCE_VALUE_MISMATCH',self.codes(r))
                self.assertIsNone(r['items'][0]['common_item_candidate']['quantity'])

    def test_nonfinite_quantities_return_invalid_report_without_hash_crash(self):
        for bad in (float('inf'),float('-inf'),float('nan')):
            self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value']=bad
            r=self.prepare()
            self.assertFalse(r['valid']);self.assertIn('INVALID_QUANTITY',self.codes(r))

    def test_missing_counts_never_default_to_zero_or_order_quantity(self):
        for k in ('ordered_quantity','fulfilled_quantity','allocated_quantity','planned_input_quantity'):
            del self.interpreted['items'][0]['fields'][k]
        r=self.prepare();i=r['items'][0]
        for k in ('ordered_quantity','fulfilled_quantity','allocated_quantity'):self.assertIsNone(i['contract_item_candidate'][k])
        self.assertIsNone(i['planned_input_quantity']);self.assertIn('MISSING_VALUE',self.codes(r))

    def test_conflicting_grounded_quantities_remain_questions(self):
        self.replace_text(TEXT+'Order amendment: 120 piece\n')
        self.interpreted['items'][0]['fields']['ordered_quantity'].append(claim(self.extracted,'120',120))
        r=self.prepare();self.assertIn('CONFLICTING_VALUES',self.codes(r))
        self.assertIsNone(r['items'][0]['common_item_candidate']['quantity'])
        self.assertEqual(2,len(r['items'][0]['fields']['ordered_quantity']['claims']))

    def test_no_evidence_wrong_location_and_value_are_distinct(self):
        c=self.interpreted['items'][0]['fields']['ordered_quantity'][0];c['evidence']=[]
        self.assertIn('EVIDENCE_MISSING',self.codes(self.prepare()))
        c.update(claim(self.extracted,'100',100));c['evidence'][0]['start']=0
        self.assertIn('EVIDENCE_LOCATION_MISMATCH',self.codes(self.prepare()))
        c.update(claim(self.extracted,'100',200))
        self.assertIn('EVIDENCE_VALUE_MISMATCH',self.codes(self.prepare()))

    def test_unknown_segment_and_fabricated_quote_are_rejected(self):
        c=self.interpreted['items'][0]['fields']['ordered_quantity'][0]
        c['evidence'][0]['segment_id']='text:line:999'
        self.assertIn('EVIDENCE_LOCATION_MISMATCH',self.codes(self.prepare()))
        c.update(claim(self.extracted,'100',100));c['evidence'][0]['quote']='200'
        self.assertIn('EVIDENCE_LOCATION_MISMATCH',self.codes(self.prepare()))

    def test_tampered_source_hash_bytes_text_segments_and_interpretation_hash(self):
        for what in ('hash','bytes','text','segments'):
            e=copy.deepcopy(self.extracted)
            if what=='hash':e['source']['sha256']='0'*64
            if what=='bytes':e['source_bytes']+=b'changed'
            if what=='text':e['text']+='changed'
            if what=='segments':e['segments'][0]['text']='changed'
            self.assertFalse(intake.prepare_contract_demand(e,self.interpreted,self.bundle)['valid'],what)
        self.interpreted['source_sha256']='0'*64
        self.assertIn('SOURCE_HASH_MISMATCH',self.codes(self.prepare()))

    def test_unstated_constraints_are_not_permissions(self):
        r=self.prepare();i=r['items'][0]
        self.assertEqual('unspecified',i['contract_item_candidate']['constraints_state'])
        self.assertIsNone(i['permissions']['revision_change']);self.assertIsNone(i['permissions']['substitution'])
        self.assertIn('CONSTRAINTS_UNCONFIRMED',self.codes(r))

    def test_bad_date_and_naive_datetime_are_rejected(self):
        for due in ('2026-02-30','2026-11-10T12:00:00'):
            self.replace_text(TEXT.replace('2026-11-10',due))
            self.interpreted['items'][0]['fields']['delivery_due']=[claim(self.extracted,due)]
            self.assertIn('INVALID_TIME',self.codes(self.prepare()))

    def test_offset_datetime_is_preserved(self):
        due='2026-11-10T12:30:00+09:00';self.replace_text(TEXT.replace('2026-11-10',due))
        self.interpreted['items'][0]['fields']['delivery_due']=[claim(self.extracted,due)]
        c=self.prepare()['items'][0]['contract_item_candidate']
        self.assertEqual((due,'datetime'),(c['delivery_due'],c['delivery_due_precision']))

    def test_wrong_catalog_version_and_relationship_are_errors(self):
        self.bundle['data_version']=2
        self.assertIn('REFERENCE_VERSION_MISMATCH',self.codes(self.prepare()))
        self.bundle=catalog_bundle();self.bundle['candidates'][0]['revision']['model_id']='missing:model'
        self.assertIn('CONFIGURATION_RELATION_MISMATCH',self.codes(self.prepare()))

    def test_absent_configuration_and_inconsistent_selection_are_rejected(self):
        for cid in ('config:does-not-exist','config:esp32-poe:L1:WROOM'):
            self.interpreted['items'][0]['proposed_configuration_id']=cid
            self.assertIn('CONFIGURATION_SELECTION_MISMATCH',self.codes(self.prepare()))
        self.bundle['candidates']=[]
        self.assertIn('CONFIGURATION_NOT_FOUND',self.codes(self.prepare()))

    def test_dict_confirmation_claim_cannot_confirm_demand(self):
        self.interpreted.update(confirmed=True,confirmation_ref='arbitrary',confirmation={'confirmed':True})
        r=self.prepare();self.assertFalse(r['confirmation']['verified'])
        self.assertEqual('needs_confirmation',r['status'])
        self.assertEqual('arbitrary',r['confirmation']['supplied_claims']['confirmation_ref'])

    def test_edits_preserve_before_after_and_invalidate_binding(self):
        before=self.prepare();self.interpreted['items'][0]['fields']['ordered_quantity'][0]['value']=120
        after=self.prepare(previous=before)
        self.assertTrue(after['revision']['binding_changed']);self.assertTrue(after['confirmation']['invalidated'])
        self.assertEqual(100,after['revision']['before']['items'][0]['contract_item_candidate']['ordered_quantity'])
        self.assertIsNone(after['revision']['after']['items'][0]['contract_item_candidate']['ordered_quantity'])
        self.assertFalse(after['confirmation']['verified'])
        self.assertFalse(self.prepare(previous=self.prepare())['revision']['binding_changed'])

    def test_input_objects_are_not_mutated(self):
        snapshot=copy.deepcopy((self.extracted,self.interpreted,self.bundle));self.prepare()
        self.assertEqual(snapshot,(self.extracted,self.interpreted,self.bundle))

    def test_multiple_items_keep_independent_candidates(self):
        self.interpreted['items'].append(dict(fields={'ordered_quantity':[claim(self.extracted,'100',100)]}))
        r=self.prepare();self.assertEqual(2,len(r['items']))
        self.assertIsNone(r['items'][1]['contract_item_candidate']['configuration_id'])
        self.assertEqual(100,r['items'][1]['common_item_candidate']['quantity'])

    def test_report_includes_original_body_and_evidence_locations(self):
        r=self.prepare()
        self.assertEqual(TEXT,r['original']['text'])
        self.assertEqual(self.extracted['segments'],r['original']['segments'])

    def test_new_source_version_and_selection_each_change_confirmation_binding(self):
        before=self.prepare()
        self.replace_text(TEXT+'Additional clause.\n')
        self.assertTrue(self.prepare(previous=before)['confirmation']['invalidated'])
        before=self.prepare()
        self.interpreted['data_version']=2;self.bundle['data_version']=2
        self.assertTrue(self.prepare(previous=before)['confirmation']['invalidated'])
        before=self.prepare()
        self.interpreted['items'][0]['proposed_configuration_id']='config:esp32-poe:L:WROOM'
        self.assertTrue(self.prepare(previous=before)['confirmation']['invalidated'])

    def test_unsupported_format_and_encoding_are_explicit(self):
        p=self.path.with_suffix('.docx');p.write_bytes(b'unknown')
        self.assertFalse(intake.extract_contract_text(p)['supported'])
        self.path.write_bytes(b'\xff\xfe');self.assertFalse(intake.extract_contract_text(self.path)['supported'])


class PublicQueryBoundaryTests(IntakeFixture,unittest.TestCase):
    def test_failed_lookup_preserves_envelope_and_never_returns_report(self):
        response=dict(ok=False,data=None,data_version=1,errors=[{'code':'NOT_FOUND'}],run_id='failure-run')
        class Session:
            def call(self,tool,args):return copy.deepcopy(response)
        r=intake.read_contract_demand(Session(),{'data_version':1},self.extracted,self.interpreted)
        self.assertIsNone(r['report']);self.assertEqual(response,r['query']['lookup_response'])
        self.assertIsNone(r['query']['configurations'])

    def test_public_version_and_relationship_mismatch_return_no_report(self):
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes'];bundle=self.bundle
        class Session:
            def __init__(self,wrong=False):self.wrong=wrong
            def call(self,tool,args):
                if args['version']!=1:raise AssertionError('Exact version required')
                if tool=='lookup':data=copy.deepcopy(nodes)
                else:
                    data=copy.deepcopy(next(c for c in bundle['candidates'] if c['configuration']['id']==args['configuration_id']))
                    data['revision']['model_id']='wrong:model'
                return dict(ok=True,data=data,data_version=2 if self.wrong else 1,errors=[],run_id='test-only')
        for s,code in [(Session(True),'REFERENCE_VERSION_MISMATCH'),(Session(),'CONFIGURATION_RELATION_MISMATCH')]:
            r=intake.read_contract_demand(s,{'data_version':1},self.extracted,self.interpreted)
            self.assertIsNone(r['report']);self.assertIn(code,{x['code'] for x in r['query']['errors']})

    def test_malformed_bom_detail_returns_query_error_without_exception(self):
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes'];bundle=self.bundle
        class Session:
            def call(self,tool,args):
                if tool=='lookup':data=copy.deepcopy(nodes)
                else:
                    data=copy.deepcopy(next(c for c in bundle['candidates'] if c['configuration']['id']==args['configuration_id']))
                    del data['items'][0]['id']
                return dict(ok=True,data=data,data_version=1,errors=[])
        r=intake.read_contract_demand(Session(),{'data_version':1},self.extracted,self.interpreted)
        self.assertIsNone(r['report'])
        self.assertIn('CONFIGURATION_RELATION_MISMATCH',{x['code'] for x in r['query']['errors']})

    def test_failed_configuration_does_not_return_partial_catalog(self):
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        class Session:
            def call(self,tool,args):return dict(ok=tool=='lookup',data=nodes if tool=='lookup' else None,
                data_version=1,errors=[] if tool=='lookup' else [{'code':'DB_FAILURE'}],run_id='test-only')
        r=intake.read_contract_demand(Session(),{'data_version':1},self.extracted,self.interpreted)
        self.assertIsNone(r['report']);self.assertIsNone(r['query']['configurations'])
        self.assertFalse(r['query']['configuration_responses'][0]['ok'])

    def test_interpretation_cannot_replace_host_data_version(self):
        class Session:
            def call(self,*args):raise AssertionError('Must not query with mismatched input')
        self.interpreted['data_version']=2
        r=intake.read_contract_demand(Session(),{'data_version':1},self.extracted,self.interpreted)
        self.assertIsNone(r['report']);self.assertIn('REFERENCE_VERSION_MISMATCH',{x['code'] for x in r['query']['errors']})


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Explicit isolated TEST_DATABASE_URL required')
class PublicPostgresIntakeTests(IntakeFixture,unittest.TestCase):
    def test_real_public_session_exact_version_and_reconnect(self):
        from tests.db import database_for_test
        from ontologics.production_planning.host import PlanningHost as CommonHost
        from ontologics.identity import DevelopmentIdentityProvider,UserIdentity,DevelopmentContext
        from ontologics.production_planning.persistence import PlanningService as Service
        settings=database_for_test(self);nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
        seed=Service(settings,principals={'fixture':{'kind':'human','roles':['seed','read']}})
        try:self.assertTrue(seed.bootstrap('fixture',nodes,request_key='task02-seed')['ok'])
        finally:seed.close()
        host=CommonHost(DevelopmentIdentityProvider([UserIdentity('task02-reader',frozenset({'read'}))],enabled=True),settings)
        for _ in range(2):
            with host.request(DevelopmentContext('task02-reader')) as session:
                r=intake.read_contract_demand(session,{'data_version':1},self.extracted,self.interpreted)
            self.assertTrue(r['report']['valid'],r)
            self.assertEqual('config:esp32-poe:L:WROOM',r['report']['items'][0]['contract_item_candidate']['configuration_id'])
            self.assertEqual(1,r['query']['lookup_response']['data_version'])
            self.assertEqual(2,len(r['query']['configuration_responses']));self.assertFalse(r['report']['confirmation']['verified'])
        with host.request(DevelopmentContext('task02-reader')) as session:
            bad=copy.deepcopy(self.interpreted);bad['data_version']=999
            r=intake.read_contract_demand(session,{'data_version':999},self.extracted,bad)
            self.assertIsNone(r['report']);self.assertFalse(r['query']['lookup_response']['ok'])


try:
    import pypdf
except ImportError:
    pypdf = None


def write_text_pdf(path, pages):
    """Small disposable ASCII PDF fixture, not an operational contract."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter()
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    for text in pages:
        page=writer.add_blank_page(width=612,height=792)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject()
        commands=['BT /F1 12 Tf 50 750 Td']
        for line in text.splitlines():
            line=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            commands.append('('+line+') Tj 0 -18 Td')
        commands.append('ET');stream.set_data('\n'.join(commands).encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path)


@unittest.skipUnless(pypdf,'Optional cached pypdf unavailable')
class PdfIntakeTests(IntakeFixture,unittest.TestCase):
    def test_same_contract_pdf_retains_page_and_exact_quantity_evidence(self):
        path=self.path.with_suffix('.pdf');write_text_pdf(path,[TEXT])
        e=intake.extract_contract_text(path)
        self.assertTrue(e['supported'],e)
        r=intake.prepare_contract_demand(e,interpretation(e),self.bundle)
        self.assertTrue(r['valid'],r);self.assertEqual(100,r['items'][0]['common_item_candidate']['quantity'])
        ev=r['items'][0]['fields']['ordered_quantity']['claims'][0]['evidence'][0]
        self.assertEqual('pdf:page:1:line:5',ev['segment_id'])
        self.assertEqual(1,e['segments'][4]['page'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),e['source']['sha256'])

    def test_multiple_pages_keep_distinct_locators(self):
        path=self.path.with_suffix('.pdf');write_text_pdf(path,[TEXT,'Amendment: 120 piece'])
        e=intake.extract_contract_text(path)
        self.assertEqual(2,e['page_count']);self.assertEqual(2,e['segments'][-1]['page'])
        interpreted=interpretation(e)
        interpreted['items'][0]['fields']['ordered_quantity'].append(claim(e,'120',120))
        r=intake.prepare_contract_demand(e,interpreted,self.bundle)
        self.assertIn('CONFLICTING_VALUES',self.codes(r))

    def test_scan_or_mixed_blank_pages_require_ocr_instead_of_empty_success(self):
        path=self.path.with_suffix('.pdf')
        for pages in ([''],[TEXT,'']):
            write_text_pdf(path,pages);e=intake.extract_contract_text(path)
            self.assertFalse(e['supported']);self.assertEqual('NO_TEXT_OR_OCR_REQUIRED',e['errors'][0]['code'])

    def test_corrupt_and_encrypted_pdf_are_unsupported(self):
        path=self.path.with_suffix('.pdf');path.write_bytes(b'%PDF-1.7\ninvalid')
        self.assertEqual('PDF_EXTRACTION_FAILED',intake.extract_contract_text(path)['errors'][0]['code'])
        writer=pypdf.PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('test-only');writer.write(path)
        self.assertEqual('ENCRYPTED_PDF_UNSUPPORTED',intake.extract_contract_text(path)['errors'][0]['code'])


class ContractExampleTests(unittest.TestCase):
    def test_fixed_example_interpretation_is_explicit_and_preserves_current_contract(self):
        if importlib.util.find_spec('examples.production_planning_contract_intake') is None:
            self.fail('Task 02 executable example is not implemented')
        from examples.production_planning_contract_intake import reference_interpretation
        e=intake.extract_contract_text(ROOT/'data/production_planning/contract.txt')
        i=reference_interpretation(e,data_version=7)
        bundle=catalog_bundle();bundle['data_version']=7
        r=intake.prepare_contract_demand(e,i,bundle)
        self.assertTrue(r['valid'],r)
        self.assertEqual(100,r['items'][0]['planned_input_quantity'])
        self.assertEqual(0,r['items'][0]['contract_item_candidate']['fulfilled_quantity'])
        self.assertEqual('date',r['items'][0]['contract_item_candidate']['delivery_due_precision'])
        self.assertFalse(r['confirmation']['verified'])
        e['source']['sha256']='0'*64
        with self.assertRaises(ValueError):reference_interpretation(e,data_version=7)
