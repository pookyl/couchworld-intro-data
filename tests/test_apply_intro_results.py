"""Persist only usable candidates, preserving existing files and episode exceptions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from apply_intro_results import apply_results, proposed_marker


def row():
    return {'vod_id':123,'title':'Example','processing_status':'analyzed',
            'conservative_review':{'status':'review_required','episodes':[
                {'file':f'123-{e}.wav','review_target_seconds':t,
                 'common_index_span':[0,100],'preserve_cold_open':False}
                for e,t in [(2,91),(3,90),(4,90)]]},
            'episode_card_review':{'status':'not_confirmed'}}


class ApplyResultsTest(unittest.TestCase):
    def test_audio_candidate_does_not_require_ocr_and_uses_earliest_end(self):
        self.assertEqual(proposed_marker(row()),({'start':0,'end':90},None))
        r=row();r['episode_card_review']['status']='timing_mismatch'
        self.assertEqual(proposed_marker(r)[0]['end'],90)

    def test_incomplete_conflicting_and_cold_open_candidates_are_deferred(self):
        cases=[]
        r=row();r['conservative_review']['episodes'].pop();cases.append((r,'incomplete_audio_evidence'))
        r=row();r['episode_card_review']['status']='conflict';cases.append((r,'episode_number_conflict'))
        r=row();r['conservative_review']['episodes'][0]['common_index_span']=[10,100];cases.append((r,'unmatched_opening_content'))
        r=row();r['conservative_review']['episodes'][0]['review_target_seconds']=99;cases.append((r,'episode_time_difference'))
        r=row();r['conservative_review']['episodes'][0]['review_target_seconds']=float('nan');cases.append((r,'invalid_candidate'))
        for r,reason in cases:
            self.assertEqual(proposed_marker(r),(None,reason))

    def test_default_writes_whole_series_and_no_fake_episode_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);report={'series':[row()]}
            counts=apply_results(report,root)
            self.assertEqual(counts['written'],1)
            saved=json.loads((root/'123.json').read_text())
            self.assertEqual(saved['defaultIntro'],{'start':0,'end':90})
            self.assertEqual(saved['episodes'],{})
            self.assertTrue(report['production_markers_changed'])

    def test_force_preserves_episode_overrides_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'123.json'
            original={'schemaVersion':1,'provider':'olevod','vodId':123,'defaultIntro':{'start':0,'end':110},
                      'episodes':{'1':{'disabled':True}},'notes':'keep'}
            p.write_text(json.dumps(original));before=p.read_bytes()
            self.assertEqual(apply_results({'series':[row()]},root)['skipped_existing'],1)
            self.assertEqual(p.read_bytes(),before)
            self.assertEqual(apply_results({'series':[row()]},root,force=True)['written'],1)
            saved=json.loads(p.read_text())
            self.assertEqual(saved['episodes'],original['episodes']);self.assertEqual(saved['notes'],'keep')
            self.assertEqual(saved['defaultIntro']['end'],90)
            self.assertEqual(apply_results({'series':[row()]},root,force=True)['unchanged'],1)

    def test_failed_detection_never_replaces_old_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'123.json';p.write_text('keep')
            r=row();r['conservative_review']['episodes']=[]
            self.assertEqual(apply_results({'series':[r]},root,force=True)['deferred'],1)
            self.assertEqual(p.read_text(),'keep')

    def test_corrupt_existing_marker_is_not_overwritten_even_with_force(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'123.json';p.write_text('bad json')
            self.assertEqual(apply_results({'series':[row()]},root,force=True)['error'],1)
            self.assertEqual(p.read_text(),'bad json')

    def test_report_only_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertEqual(apply_results({'series':[row()]},root,report_only=True)['report_only'],1)
            self.assertEqual(list(root.iterdir()),[])


if __name__=='__main__':
    unittest.main()
