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


if __name__ == '__main__':
    unittest.main()
