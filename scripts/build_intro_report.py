#!/usr/bin/env python3
"""Render a self-contained local HTML review report, with bounded video stills."""
import argparse
import base64
import concurrent.futures
from datetime import datetime
import html
import json
from pathlib import Path
import statistics
import subprocess


def clock(seconds):
    seconds = int(seconds)
    return f'{seconds // 60}:{seconds % 60:02}'


def escape(value):
    return html.escape(str(value))


def targets(row):
    return {item['file']:item for item in row['conservative_review']['episodes']}


def summary(row):
    if row.get('processing_status') == 'skipped_existing':
        return '已跳过'
    if row.get('processing_status') == 'metadata_error':
        return '详情不可用'
    if row.get('processing_status') == 'analysis_error':
        return '分析失败'
    found = targets(row)
    if not found:
        return '暂不能确定'
    values = [e['review_target_seconds'] for e in found.values()]
    if len(set(values)) == 1:
        return clock(values[0])
    return ' / '.join(clock(value) for value in values)


def marker_write_text(row):
    write = row.get('marker_write', {})
    labels = {'written': '已写入', 'unchanged': '已是相同值', 'skipped_existing': '已有标注，跳过',
              'deferred': '待复核，未写入', 'report_only': '仅报告，未写入', 'error': '写入失败'}
    label = labels.get(write.get('status'), '未执行写入')
    return label + ('：' + write['reason_note'] if write.get('reason_note') else '')


def reason(row):
    if row.get('processing_status') == 'metadata_error':
        return '详情不可用或本地元数据缺失，本次未取样。'
    if row.get('processing_status') == 'analysis_error':
        return '分析失败，请检查工具依赖和样本。'
    if row.get('reason_note'):
        return row['reason_note']
    found = list(targets(row).values())
    if not found:
        return '未取得三组完整且互相重叠的匹配；不能据此认定没有片头。'
    notes = ['候选已从较早共同末端回退 3 秒；需复核。']
    if max(x['pair_end_spread_seconds'] for x in found)>5:
        notes.append('不同配对的末端差异较大，重复广告等内容可能影响匹配。')
    if any(x['preserve_cold_open'] for x in found):
        notes.append('重复片段前有未匹配内容，不能从 0 秒一律跳过。')
    return ''.join(notes)


def render_frame(job):
    media, second, destination, reuse = job
    if reuse and destination.exists() and destination.stat().st_size:
        return 'data:image/jpeg;base64,' + base64.b64encode(destination.read_bytes()).decode()
    result = subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(second),'-i',str(media),
                             '-frames:v','1','-vf','scale=320:-2',str(destination)],capture_output=True,timeout=20)
    if result.returncode or not destination.exists():
        return None
    return 'data:image/jpeg;base64,' + base64.b64encode(destination.read_bytes()).decode()


def episode_card_section(row):
    review = row.get('episode_card_review')
    if not review:
        return ''
    labels = {'confirmed': '三集集数卡片确认', 'partial': '仅部分样本确认',
              'conflict': '识别到不符的集数，需复核', 'timing_mismatch': '集数正确但出现时间不一致',
              'unavailable': 'OCR 未能运行', 'skipped': '本次跳过 OCR',
              'no_audio_candidate': '无音频候选，未扫描', 'not_confirmed': '未取得完整集数证据',
              'insufficient_samples': '样本不足'}
    rows = []
    for episode in review.get('episodes', []):
        when = episode.get('first_confirmed_seconds')
        texts = ' / '.join(dict.fromkeys(h['text'] for h in episode.get('hits', [])))
        conflicts = ' / '.join(dict.fromkeys(h['text'] for h in episode.get('mismatches', [])))
        rows.append(f'<tr><td>第 {episode["episode"]} 集</td><td>{clock(when) if when is not None else "未确认"}</td><td>{escape(texts or "—")}</td><td>{escape(conflicts or "—")}</td></tr>')
    label = labels.get(review['status'], review['status'])
    return f'<h3>自动集数识别：{escape(label)}</h3><p>核对实际集数，要求连续两帧命中，三集出现时间相差不超过 5 秒。用于确认片头区域，不把跳转点向后移动。未识别到不代表没有片头。</p><table><tr><th>样本</th><th>首次连续命中</th><th>识别文字</th><th>冲突文字</th></tr>{"".join(rows)}</table>'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reuse-frames',action='store_true',help='Reuse stills from this same saved sample set')
    args=parser.parse_args()
    report=json.loads(args.input.read_text())
    accepts_ads=report.get('acceptance_policy',{}).get('id')=='opening_or_post_intro_ad'
    accepted=sum(row.get('assessment_status')=='acceptable_sample_candidate' for row in report['series'])
    def verdict(row):
        if row.get('assessment_status')!='acceptable_sample_candidate':
            return '暂不能确定'
        return '可接受 · 保留部分广告' if row['visual_review_status']=='advertisement_frame' else '可接受 · 保留片头尾部'
    root=args.input.parent
    frames=root/'report-frames'
    frames.mkdir(exist_ok=True)
    jobs=[]
    displays={}
    for row in report['series']:
        found=targets(row)
        items=[]
        for sample in row['samples']:
            if sample['status']!='complete':
                continue
            name=f'{row["vod_id"]}-{sample["episode"]}'
            match=found.get(name+'.wav')
            times=[max(0,match['review_target_seconds']-3),match['review_target_seconds'],min(179,match['review_target_seconds']+3)] if match else [30,90,150]
            for second in times:
                items.append((len(jobs),sample['episode'],second,bool(match)))
                jobs.append((root/(name+'.mkv'),second,frames/f'{name}-{second}.jpg',args.reuse_frames))
        displays[row['vod_id']]=items
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        images=list(pool.map(render_frame,jobs))
    complete=[s for row in report['series'] for s in row['samples'] if s['status']=='complete']
    fresh=[s for s in complete if not s.get('reused') and not s.get('cached_this_invocation')]
    identified=sum(bool(targets(row)) for row in report['series'])
    table=''.join(f'<tr><td>{r["rank"]}</td><td><a href="#vod-{r["vod_id"]}">{escape(r["title"])}</a><small>{r["vod_id"]}</small></td><td class="time">{summary(r)}</td>'+ (f'<td>{verdict(r)}</td>' if accepts_ads else '') +f'<td>{escape(reason(r))}</td><td>{escape(marker_write_text(r))}</td></tr>' for r in report['series'])
    cards=[]
    for row in report['series']:
        figures=''.join(f'<figure><img loading="lazy" src="{images[index]}" alt="第 {ep} 集 {clock(second)} 截图"><figcaption>第 {ep} 集 · {clock(second)} · {"候选附近" if matched else "窗口概览"}</figcaption></figure>' for index,ep,second,matched in displays[row['vod_id']] if images[index])
        pair_rows=[]
        for pair in row.get('audio_analysis',{}).get('pairs',[]):
            label=' ↔ '.join('第 '+Path(pair[key]).stem.rsplit('-',1)[1]+' 集' for key in ('first','second'))
            candidates=pair['candidates']
            detail='未检出 ≥20 秒的重复片段'
            if candidates:
                c=candidates[0]
                detail=' / '.join(f'{v[0]:.2f}–{v[1]:.2f} 秒' for v in (c['first_index_span_seconds'],c['second_index_span_seconds']))
                detail+=f'；平均差异 {c["mean_bit_errors"]:.2f}/32 位'
                if c['touches_window_end']:
                    detail+='；触及取样边界，不视为完整片头'
            pair_rows.append(f'<tr><td>{label}</td><td>{escape(detail)}</td></tr>')
        if row.get('processing_status') in {'skipped_existing', 'metadata_error'}:
            cards.append(f'<section id="vod-{row["vod_id"]}"><h2>{escape(row["title"])}</h2><p>{escape(reason(row))}</p></section>')
            continue
        review=row.get('visual_review_note','截图用于复核，尚不能自动认定其语义为片头。')
        episode_label = ' / '.join(str(e) for e in row.get('selected_episodes', [s['episode'] for s in row['samples']]))
        cards.append(f'<section id="vod-{row["vod_id"]}"><div class="section-top"><h2>{row["rank"]:02} · {escape(row["title"])}</h2><span class="badge">{summary(row)}</span></div><p>样本：高清第 {escape(episode_label)} 集，每集前 180 秒。下图每行对应一集。</p><p class="note">{escape(review)}</p><div class="frames">{figures}</div>{episode_card_section(row)}<details><summary>查看音频配对证据（指纹坐标）</summary><table>{"".join(pair_rows)}</table></details></section>')
    download_times=[s['fetch_seconds'] for s in fresh]
    total_analysis=sum(r.get('audio_analysis',{}).get('analysis_seconds',0) for r in report['series'])
    control=report.get('cross_series_control',{})
    controls=f'跨剧对照：{control.get("pairs_tested",0)} 组，{len(control.get("matches",[]))} 组检出长重复。' if control else ''
    stats=f'新增下载 {len(fresh)} 段，复用 {len(complete)-len(fresh)} 段；新增下载耗时 {min(download_times):.1f}–{max(download_times):.1f} 秒/段，中位数 {statistics.median(download_times):.1f} 秒。' if download_times else ('全部样本来自核对后的缓存。' if complete else '本次没有完成样本。')
    document='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>连续剧周榜前十 · 片头算法验证报告</title><style>
    :root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#23352e;background:#f5f7f3}*{box-sizing:border-box}body{margin:0}main{max-width:1120px;margin:auto;padding:48px 24px}h1{font-size:clamp(28px,5vw,42px);letter-spacing:-.04em;margin:12px 0}h2{font-size:23px;margin:0}p{line-height:1.8;color:#52645b}.eyebrow{font-size:13px;letter-spacing:.12em;color:#38624f;font-weight:700}.metrics{display:flex;gap:16px;margin:28px 0;flex-wrap:wrap}.metric{background:white;padding:20px 24px;border:1px solid #dce4da;border-radius:12px;flex:1;min-width:160px}.metric b{font-size:30px;display:block}.metric span{color:#65766c;font-size:14px}section,.summary-box{background:white;border:1px solid #dce4da;border-radius:14px;padding:24px;margin:24px 0}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:14px 10px;border-bottom:1px solid #e6ebe4;text-align:left;vertical-align:top}th{color:#617267;font-weight:600}td small{display:block;color:#7b8a80;margin-top:5px}.time{white-space:nowrap;font-weight:700}a{color:#286446;text-underline-offset:3px}.frames{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:20px 0}figure{margin:0;background:#f4f6f3;border-radius:8px;overflow:hidden}img{width:100%;display:block;aspect-ratio:16/9;object-fit:contain;background:#101913}figcaption{padding:9px;font-size:12px;color:#52645b}.section-top{display:flex;justify-content:space-between;gap:15px;align-items:center}.badge{background:#ecf3e8;padding:7px 12px;border-radius:20px;white-space:nowrap;font-weight:650}.note{background:#faf5e9;padding:12px 15px;border-left:3px solid #bb9653}details{margin-top:18px}summary{cursor:pointer;color:#38624f}footer{font-size:13px;color:#6d7d72;line-height:1.8}.table-wrap{overflow:auto}@media(max-width:600px){main{padding:28px 14px}section,.summary-box{padding:16px}.frames{gap:5px}figcaption{padding:5px;font-size:10px}.section-top{align-items:flex-start}h2{font-size:19px}}
    </style></head><body><main>'''
    count = len(report['series'])
    skipped = sum(r.get('processing_status') == 'skipped_existing' for r in report['series'])
    expected = (count - skipped) * 3
    period = {'day': '日榜', 'week': '周榜', 'month': '月榜', 'all': '总榜'}.get(report.get('ranking_period', 'week'), '榜单')
    selection = ('单剧检测' if report.get('selection_mode') == 'single' else f'连续剧{period}前 {report.get("requested_limit", count)} 名')
    title = selection + (' · 保守跳转报告' if accepts_ads else ' · 片头识别验证')
    document = document.replace('<title>连续剧周榜前十 · 片头算法验证报告</title>', f'<title>{escape(title)}</title>')
    stamp=datetime.fromisoformat(report['ranking_fetched_at']).strftime('%Y-%m-%d %H:%M')
    document+=f'<div class="eyebrow">COUCHWORLD / 实验报告 / 高清片源</div><h1>{title}</h1><p>数据时间：{stamp}（洛杉矶时间）<br>按每部列出的取样集数，每集只分析前 3 分钟。宁可多留片头，不向后寻找清晰画面作为跳转点。</p>'
    writes = report.get('marker_write_summary')
    if writes:
        document += f'<p><strong>API 标注：写入 {writes["written"]} 部，相同值 {writes["unchanged"]} 部，待复核 {writes["deferred"]} 部，写入失败 {writes["error"]} 部。</strong></p>'
    metric_label='部符合本次样本标准' if accepts_ads else '部产生三集共同候选'
    metric_count=accepted if accepts_ads else identified
    document+=f'<div class="metrics"><div class="metric"><b>{count}</b><span>部连续剧</span></div><div class="metric"><b>{len(complete)} / {expected}</b><span>完整样本</span></div><div class="metric"><b>{metric_count}</b><span>{metric_label}</span></div><div class="metric"><b>−3 秒</b><span>从较早共同末端回退</span></div></div>'
    document += f'<p>选中 {count} 部；已有标注跳过 {skipped} 部；本次待处理 {count-skipped} 部。榜单名次保留，跳过后不递补。</p>'
    if accepts_ads:
        document+='<p class="note"><strong>本次标准：保留少量片头或片头后的广告均可接受，以保留正片为优先。</strong>候选落在赞助广告中不再判为失败，也不为去掉广告继续后移。缺少三集共同证据的剧暂不给统一时间。“可接受”仅指本次所选集数样本，不能保证全剧每集都适用。</p><p>取样集数和截图见各剧详情；本地标注写入状态见结果表。</p>'
    else:
        document+='<p class="note"><strong>检测时间与正式标注写入状态分别列出。</strong>指纹检测的是重复音频，广告、集数字幕及相同配乐可能混入；从指纹坐标回退 3 秒也不能保证不跳剧情。无法形成三集共同证据时不建议跳转。本地标注写入状态见结果表；线上数据需提交推送后更新。</p>'
    if report.get('review_counts'):
        counts=report['review_counts']
        if accepts_ads:
            document+=f'<p><strong>{accepted} 部可接受：{counts["opening_frame"]} 部保留片头尾部，{counts["advertisement_frame"]} 部保留部分广告；另 {counts["insufficient_evidence"]} 部暂不能确定。</strong>画面类别由人工复核，音频算法负责寻找重复片段。</p>'
        else:
            document+=f'<p><strong>截图复核：{counts["opening_frame"]} 部候选仍在片头画面，{counts["advertisement_frame"]} 部落在广告中，{counts["insufficient_evidence"]} 部无法形成三集共同候选。</strong>截图判断由人工完成，不能等同于自动算法已识别广告或正片。</p>'
    verdict_heading='<th>结论</th>' if accepts_ads else ''
    document+=f'<div class="summary-box"><h2>结果一览</h2><p>不同集有不同候选时，按各剧所列取样集数顺序列出。</p><div class="table-wrap"><table><thead><tr><th>序号</th><th>剧名 / ID</th><th>保守候选</th>{verdict_heading}<th>判断依据</th><th>本地标注</th></tr></thead><tbody>{table}</tbody></table></div></div>'
    document+=f'<div class="summary-box"><h2>方法与耗时</h2><p>FFmpeg 提取单声道音频 → Chromaprint 1.6.2 音频指纹 → 三组跨集匹配 → 每集取两组匹配的共同区间 → 末端向下取整并减 3 秒。仅保留 ≥20 秒且未触及取样尾端的共同重复。没有使用已有人工标注作为输入。</p><p>{stats}<br>流程耗时 {report["wall_seconds"]:.1f} 秒（不含制图与人工复核）；本次音频分析合计 {total_analysis:.2f} 秒。{controls}</p><p>指纹窗口缓冲约 2.6 秒，输出并非逐帧边界。检测不到不代表没有片头，也可能片头位于 3 分钟之后、三集版本不同或匹配条件过严。取样用于推断共同片头；确认后默认应用到整部剧（包括第 1 集），已有单集例外优先。全剧应用不代表逐集验证。画面为本次下载样本的原始抽帧，未调亮或修改。</p></div>'
    document+=''.join(cards)
    document+='<footer>仅本地验证报告 · 没有启用自动发布或 GitHub Actions 定时任务 · 媒体链接及访问参数不包含在报告内</footer></main></body></html>'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(document)
    print(f'Wrote {args.output}; {sum(image is not None for image in images)}/{len(images)} review frames embedded.')


if __name__=='__main__':
    main()
