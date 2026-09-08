from pathlib import Path
import json, statistics
root=Path('/tmp/click-review-hardening-20260908/report-cleanup')
reports={version:[json.loads((root/f'pair-{pair}-{version}.json').read_text()) for pair in (1,2)] for version in ('baseline','current')}
def dist(v):
    return {'samples':v,'median':statistics.median(v),'min':min(v),'max':max(v),'sample_stdev':statistics.stdev(v) if len(v)>1 else None}
summary=[]
for index in range(5):
    case={'matching':reports['baseline'][0]['cases'][index]['existing_matching_files'],'nonmatching':reports['baseline'][0]['cases'][index]['nonmatching_files']}
    for version in reports:
        selected=[report['cases'][index] for report in reports[version]]
        case[version]={}
        for mode in ('inline','fallback'):
            case[version][mode]={'wall_ms':dist([v for c in selected for v in c[mode]['wall_ms']['samples']]),'peak_python_bytes':dist([v for c in selected for v in c[mode]['peak_python_bytes']['samples']]),'block_wall_medians_ms':[c[mode]['wall_ms']['median'] for c in selected],'operations':selected[0][mode]['operations']}
            assert selected[0][mode]['operations']==selected[1][mode]['operations']
    b,c=case['baseline']['fallback']['wall_ms']['median'],case['current']['fallback']['wall_ms']['median']
    case['fallback_delta_ms_baseline_minus_current']=b-c
    summary.append(case)
result={'method':'Two blocks, order baseline-current then current-baseline; 2 warmups and 11 timed samples per mode/case/block, separate3 peak samples; retained original phase0 samples not reused as paired control. No other task heavy tests during these measurements. Python 3.12.3/Linux; full arrays are descriptive samples, not confidence bounds. Measurements are warm in-process report components only.','cases':summary,'starvation':{v:reports[v][0]['starvation'] for v in reports},'source_sha256':{v:reports[v][0]['environment']['gate_sha256'] for v in reports}}
(root/'paired-summary.json').write_text(json.dumps(result,indent=2)+'\n')
for case in summary:
    print(json.dumps({'matching':case['matching'],'nonmatching':case['nonmatching'],'baseline_ms':case['baseline']['fallback']['wall_ms']['median'],'current_ms':case['current']['fallback']['wall_ms']['median'],'baseline_peak':case['baseline']['fallback']['peak_python_bytes']['median'],'current_peak':case['current']['fallback']['peak_python_bytes']['median'],'baseline_ops':case['baseline']['fallback']['operations'],'current_ops':case['current']['fallback']['operations']}))
print(json.dumps(result['starvation']))
