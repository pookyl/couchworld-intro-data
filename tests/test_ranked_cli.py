"""CLI selection, skip/force semantics and variable-size reports."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    import numpy
except ImportError:
    numpy = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
if numpy is not None:
    import validate_ranked_intros as cli
import build_intro_report as renderer


@unittest.skipIf(numpy is None, 'Optional NumPy dependency is not installed')
class RankedCLITest(unittest.TestCase):
    def test_defaults_and_all_periods(self):
        args = cli.parse_args([])
        self.assertEqual((args.period, args.limit, args.force), ('week', 10, False))
        for period in ('day', 'week', 'month', 'all'):
            args = cli.parse_args(['--period', period, '--limit', '20'])
            with patch.object(cli, 'api', return_value=[{'id':1,'name':'a','hits':10}]) as api, contextlib.redirect_stdout(io.StringIO()):
                source = cli.select_source(args)
            api.assert_called_once_with(f'/v1/pub/index/vod/data/rank/{period}/2/20')
            self.assertEqual(source['period'], period)

    def test_links_and_invalid_combinations(self):
        for link in ['https://www.olevod.com/details-2-85528.html，',
                     'https://www.olevod.com/player/vod/2-85528-9.html']:
            self.assertEqual(cli.parse_args([link]).vod_id, 85528)
        self.assertEqual(cli.parse_args(['--url','https://www.olevod.com/details-2-85528.html']).vod_id,85528)
        invalid = [['--limit','0'], ['--limit','-2'], ['--period','year'],
                   ['--home-hot','--period','week'],
                   ['--home-hot','https://www.olevod.com/details-2-85528.html'],
                   ['--home-hot','--url','https://www.olevod.com/details-2-85528.html'],
                   ['--home-hot','--analyze-only'],
                   ['https://evil.example/details-2-85528.html'],
                   ['https://www.olevod.com/details-1-85528.html'],
                   ['https://www.olevod.com/details-2-85528.html','--limit','20'],
                   ['https://www.olevod.com/details-2-85528.html','--snapshot','x'],
                   ['--analyze-only']]
        for argv in invalid:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.parse_args(argv)

    def test_ranking_sorts_deduplicates_and_limits_before_skipping(self):
        data = [{'id':i,'name':str(i),'hits':i} for i in range(1,26)]
        selected = cli.ranked_items(data + [data[-1]], 20)
        self.assertEqual([x['id'] for x in selected],list(range(25,5,-1)))

    def test_home_hot_keeps_page_order_and_deduplicates_before_limit(self):
        args = cli.parse_args(['--home-hot'])
        self.assertEqual((args.period, args.limit), (None, 12))
        data = [{'id':3,'name':'first','hits':1}, {'id':1,'name':'second','hits':999},
                {'id':3,'name':'duplicate'}, {'id':2,'name':'third'}]
        with patch.object(cli, 'api', return_value=data) as api, contextlib.redirect_stdout(io.StringIO()) as output:
            source = cli.select_source(args)
        api.assert_called_once_with('/v1/pub/index/vod/hot/2/0/12')
        self.assertEqual(source['mode'], 'home_hot')
        self.assertIsNone(source['period'])
        self.assertEqual([x['id'] for x in source['data']], [3,1,2])
        self.assertIn('仅返回 3 部', output.getvalue())
        self.assertEqual([x['id'] for x in cli.ranked_items(data, 2, preserve_order=True)], [3,1])

    def test_home_hot_rejects_empty_malformed_and_non_series_data(self):
        for data in [[], {}, [None], [{'id':True,'name':'bad'}],
                     [{'id':1,'name':'movie','typeId1':1}]]:
            with self.subTest(data=data), patch.object(cli, 'api', return_value=data), self.assertRaises(ValueError):
                cli.select_source(cli.parse_args(['--home-hot']))

    def test_home_hot_skip_force_snapshot_and_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'3.json').write_text('{}')
            argv = ['--home-hot','--limit','2','--markers-dir',str(root),'--work-dir',str(root/'work')]
            data = [{'id':3,'name':'first','hits':1}, {'id':1,'name':'second','hits':999},
                    {'id':2,'name':'outside limit'}]
            def response(path):
                return data if path == cli.HOME_HOT_PATH else {'lock':True}
            with patch.object(cli, 'api', side_effect=response) as api, patch.object(cli, 'collect') as collect, \
                    contextlib.redirect_stdout(io.StringIO()):
                report = cli.run(cli.parse_args(argv))
            self.assertEqual([r['vod_id'] for r in report['series']], [3,1])
            self.assertEqual(report['skipped_count'], 1)
            self.assertEqual([c.args[0] for c in api.call_args_list], [cli.HOME_HOT_PATH, '/v1/pub/vod/detail/1/true'])
            collect.assert_not_called()
            snapshot = root/'work/home-hot-snapshot.json'
            offline = cli.parse_args(argv + ['--snapshot',str(snapshot),'--analyze-only'])
            with patch.object(cli, 'api') as api, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.run(offline)['selected_count'], 2)
            api.assert_not_called()
            with self.assertRaises(ValueError):
                cli.select_source(cli.parse_args(['--snapshot',str(snapshot)]))
            with patch.object(cli, 'api', side_effect=response) as api, contextlib.redirect_stdout(io.StringIO()):
                forced = cli.run(cli.parse_args(argv + ['--force']))
            self.assertEqual(forced['skipped_count'], 0)
            self.assertIn('/v1/pub/vod/detail/3/true', [c.args[0] for c in api.call_args_list])
            with patch.object(sys, 'argv', ['report','--input',str(root/'work/results.json'),'--output',str(root/'report.html')]), contextlib.redirect_stdout(io.StringIO()):
                renderer.main()
            html = (root/'report.html').read_text()
            self.assertIn('首页最热连续剧 · 前 2 部', html)
            self.assertIn('首页热播展示顺序保留', html)
            self.assertNotIn('周榜', html)

    def test_skip_precedes_detail_download_and_dependency_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);markers=root/'markers';markers.mkdir()
            (markers/'85528.json').write_text('even a malformed file is skipped')
            args=cli.parse_args(['https://www.olevod.com/details-2-85528.html',
                                 '--work-dir',str(root/'work'),'--markers-dir',str(markers)])
            with patch.object(cli,'api') as api, patch.object(cli,'collect') as collect, \
                    patch.object(cli,'find_fpcalc') as fpcalc, contextlib.redirect_stdout(io.StringIO()):
                report=cli.run(args)
            api.assert_not_called();collect.assert_not_called();fpcalc.assert_not_called()
            self.assertEqual(report['skipped_count'],1)
            self.assertEqual(report['series'][0]['processing_status'],'skipped_existing')
            self.assertEqual((markers/'85528.json').read_text(),'even a malformed file is skipped')
            with patch.object(sys,'argv',['report','--input',str(root/'work/results.json'),'--output',str(root/'report.html')]), contextlib.redirect_stdout(io.StringIO()):
                renderer.main()
            text=(root/'report.html').read_text()
            self.assertIn('单剧检测',text);self.assertIn('已有标注跳过 1 部',text)
            self.assertNotIn('周榜前十',text)
            self.assertIn('末端向下取整，不额外回退', text)
            self.assertNotIn('减 3 秒', text)
            # Rendering an old result must retain its historical backoff policy.
            report.pop('candidate_backoff_seconds')
            (root/'work/results.json').write_text(json.dumps(report))
            with patch.object(sys,'argv',['report','--input',str(root/'work/results.json'),'--output',str(root/'report.html')]), contextlib.redirect_stdout(io.StringIO()):
                renderer.main()
            self.assertIn('末端向下取整并减 3 秒', (root/'report.html').read_text())

    def test_force_reprocesses_but_does_not_overwrite_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'85528.json').write_text('keep')
            args=cli.parse_args(['https://www.olevod.com/details-2-85528.html', '--force',
                                 '--work-dir',str(root/'work'),'--markers-dir',str(root),'--skip-ocr'])
            detail={'name':'example','urls':[{'index':e,'url':f'url-{e}'} for e in (1,2,3,4)]}
            def collect(job, *args):
                return {'vod_id':job[0],'episode':job[1],'status':'failed'}
            with patch.object(cli,'api',return_value=detail) as api, patch.object(cli,'collect',side_effect=collect) as get, \
                    patch.object(cli,'find_fpcalc',return_value='fpcalc'), contextlib.redirect_stdout(io.StringIO()):
                report=cli.run(args)
            api.assert_called_once_with('/v1/pub/vod/detail/85528/true')
            self.assertEqual(sorted(call.args[0][1] for call in get.call_args_list),[2,3,4])
            self.assertEqual(report['skipped_count'],0)
            self.assertEqual((root/'85528.json').read_text(),'keep')

    def test_main_force_writes_detected_default_into_api_and_preserves_exceptions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); markers=root/'api/v1/intros/olevod'; markers.mkdir(parents=True)
            old={'schemaVersion':1,'provider':'olevod','vodId':85528,'defaultIntro':{'start':0,'end':110},
                 'episodes':{'1':{'disabled':True}}}
            (markers/'85528.json').write_text(json.dumps(old))
            args=cli.parse_args(['https://www.olevod.com/details-2-85528.html','--force','--skip-ocr',
                                 '--markers-dir',str(markers),'--work-dir',str(root/'work')])
            detail={'name':'Example','urls':[{'index':e,'url':f'url-{e}'} for e in (2,3,4)]}
            analysis={'samples':[{'file':f'85528-{e}.wav'} for e in (2,3,4)],'pairs':[]}
            for first,second in [(2,3),(2,4),(3,4)]:
                analysis['pairs'].append({'first':f'85528-{first}.wav','second':f'85528-{second}.wav',
                    'candidates':[{'first_index_span_seconds':[0,91],'second_index_span_seconds':[0,91],
                                   'touches_window_end':False}]})
            def collected(job,*args):
                return {'vod_id':job[0],'episode':job[1],'status':'complete','reused':True}
            with patch.object(cli,'api',return_value=detail), patch.object(cli,'collect',side_effect=collected), \
                    patch.object(cli,'find_fpcalc',return_value='fpcalc'), patch.object(cli,'analyze',return_value=analysis), \
                    contextlib.redirect_stdout(io.StringIO()):
                result=cli.run(args)
            saved=json.loads((markers/'85528.json').read_text())
            self.assertEqual(saved['defaultIntro'],{'start':0,'end':91})
            self.assertEqual(result['candidate_backoff_seconds'], 0)
            self.assertEqual(saved['episodes'],old['episodes'])
            self.assertEqual(result['marker_write_summary']['written'],1)
            self.assertTrue(result['production_markers_changed'])

    def test_twenty_selected_and_all_skipped_report_is_not_top_ten(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for i in range(1,21): (root/f'{i}.json').write_text('{}')
            args=cli.parse_args(['--period','month','--limit','20','--markers-dir',str(root),'--work-dir',str(root/'work')])
            with patch.object(cli,'api',return_value=[{'id':i,'name':str(i),'hits':i} for i in range(1,26)]) as api, contextlib.redirect_stdout(io.StringIO()):
                # Select ranks 25..6; only 20..6 have markers. Return no metadata for the other 5.
                source=cli.select_source(args)
            with patch.object(cli,'select_source',return_value=source), patch.object(cli,'api',return_value={'lock':True}), contextlib.redirect_stdout(io.StringIO()):
                report=cli.run(args)
            self.assertEqual(report['selected_count'],20)
            self.assertEqual(report['skipped_count'],15)
            self.assertEqual([r['vod_id'] for r in report['series']],list(range(25,5,-1)))
            with patch.object(sys,'argv',['report','--input',str(root/'work/results.json'),'--output',str(root/'report.html')]), contextlib.redirect_stdout(io.StringIO()):
                renderer.main()
            text=(root/'report.html').read_text()
            self.assertIn('月榜前 20 名',text)
            self.assertIn('已有标注跳过 15 部',text)
            self.assertNotIn('周榜前十',text)

    def test_offline_snapshot_does_not_fetch_ranking_and_checks_period(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'snapshot.json'
            p.write_text(json.dumps({'period':'day','fetched_at':'2026-10-15T12:00:00-07:00','data':[{'id':1,'name':'one','hits':1}]}))
            args=cli.parse_args(['--snapshot',str(p),'--analyze-only','--period','day','--limit','1'])
            with patch.object(cli,'api') as api:
                self.assertEqual(cli.select_source(args)['data'][0]['id'],1)
            api.assert_not_called()
            args.period='week'
            with self.assertRaises(ValueError): cli.select_source(args)
            with self.assertRaises(ValueError):
                cli.select_source(cli.parse_args(['--home-hot','--snapshot',str(p)]))
            # Old snapshots without mode/period remain weekly rankings.
            p.write_text(json.dumps({'fetched_at':'2026-10-15T12:00:00-07:00','data':[{'id':1,'name':'one'}]}))
            with patch.object(cli, 'api') as api, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.select_source(cli.parse_args(['--snapshot',str(p)]))['period'], 'week')
            api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
