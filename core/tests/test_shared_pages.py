"""A shared complete page is already seen even when legacy metadata lacks groups."""
import copy
import unittest

import test_practice_loop as fixtures
from eju_core.exposure import shares_exposure
from eju_core.evidence import _prior_exposure, evidence_map


class SharedPageTests(unittest.TestCase):
    setUp = fixtures.PracticeLoopTests.setUp
    item = fixtures.PracticeLoopTests.item
    configure = fixtures.PracticeLoopTests.configure
    save = fixtures.PracticeLoopTests.save
    attempt = fixtures.PracticeLoopTests.attempt
    append = fixtures.PracticeLoopTests.append
    preview = fixtures.PracticeLoopTests.preview

    def link_pages(self):
        a, b = self.core.items['shared'], self.core.items['coarse']
        # Different items and paths, exact same source PDF page. Legacy entries
        # did not have exposureGroupId; no catalog migration should be required.
        b['delivery']['source_sha256'] = a['delivery']['source_sha256']
        self.assertNotEqual(a['delivery']['assets'][0]['path'], b['delivery']['assets'][0]['path'])
        self.assertNotIn('exposureGroupId', a)
        return a, b

    def test_next_and_default_candidates_deduplicate_shared_pages(self):
        self.link_pages()
        for result in (self.preview(), self.core.get_task_candidates(self.sid, subject='math1')):
            selected = {i['item_id'] for i in result['candidates']}
            self.assertEqual(len(selected & {'shared', 'coarse'}), 1)
            self.assertIn('extra', selected)

    def test_actual_exposure_blocks_sibling_without_rewriting_catalog(self):
        a, b = self.link_pages()
        before = copy.deepcopy(self.core.catalog)
        self.save(dict(id='view-one', type='exposure', itemId=a['id'], level='stem', source='codex_report'))
        for result in (self.preview(), self.core.get_task_candidates(self.sid, subject='math1')):
            self.assertFalse({'shared', 'coarse'} & {i['item_id'] for i in result['candidates']})
            self.assertIn('extra', {i['item_id'] for i in result['candidates']})
        self.assertEqual(self.core.catalog, before)
        self.attempt('answer-sibling', b['id'])
        event = next(e for e in self.core.export_json()['events'] if e['id']=='answer-sibling')
        self.assertTrue(event['seenBefore'])

    def test_historical_false_seen_flag_cannot_become_independent(self):
        a, b = self.link_pages()
        first = self.append(dict(id='old-view', type='exposure', itemId=a['id'], level='stem', source='codex_report'))
        attempt = self.append(dict(id='legacy-answer', type='attempt', itemId=b['id'], itemVersion=b['version'],
                                   answer='1', reason='synthetic reasoning', result='correct', hint='H0',
                                   seenBefore=False, guessed=False, nodes=b['nodes']))
        self.append(dict(id='legacy-reason', type='reason_review', targetId=attempt['id'], agrees=True))
        events = self.core.export_json()['events']
        self.assertTrue(_prior_exposure(events, attempt, by_id=self.core.items))
        result = evidence_map(events, list(self.core.items.values()), self.core.catalog['nodes'])
        self.assertEqual(result[b['nodes'][0]]['independent'], [])
        self.assertFalse(next(e for e in self.core.export_json()['events'] if e['id']==attempt['id'])['seenBefore'])

    def test_page_identity_is_source_and_page_specific_not_filename(self):
        a, b = self.link_pages()
        self.assertTrue(shares_exposure(a, b))
        for field, value in [('source_sha256', 'b'*64)]:
            other=copy.deepcopy(b);other['delivery'][field]=value
            self.assertFalse(shares_exposure(a, other))
        other=copy.deepcopy(b);other['delivery']['assets'][0]['pdf_page']=2
        self.assertFalse(shares_exposure(a, other))
        other=copy.deepcopy(b);other['delivery']['assets'][0]['sha256']='c'*64
        self.assertFalse(shares_exposure(a, other))
        other=copy.deepcopy(b);other['kind']='original'
        self.assertFalse(shares_exposure(a, other))

    def test_reading_material_alone_is_not_exposure(self):
        a,b=self.link_pages()
        a['sourcePath']=a['delivery']['source_url']
        before=self.core.export_json()['events']
        self.core.read_material(self.sid,a['id'],'stem')
        self.core.read_question_images(self.sid,a['id'])
        self.assertEqual(self.core.export_json()['events'],before)
        self.assertTrue({'shared','coarse'} & {i['item_id'] for i in self.preview()['candidates']})


if __name__=='__main__':
    unittest.main()
