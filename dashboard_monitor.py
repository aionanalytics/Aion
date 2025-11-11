"""Dashboard monitor (JSON + simple HTML)."""
from __future__ import annotations
import os, json, glob, datetime
import pandas as pd
from .data_pipeline import log

OUT_DIR = os.getenv('AION_NEWS_OUT_DIR', 'news_cache')
DASH_DIR = os.getenv('AION_DASH_DIR', 'dashboard')

def build_daily_dashboard():
    ev_files = sorted(glob.glob(os.path.join(OUT_DIR, 'news_events_*.parquet')), reverse=True)[:3]
    frames = []
    for p in ev_files:
        try:
            frames.append(pd.read_parquet(p))
        except Exception: pass
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        log('[dashboard_monitor] ℹ️ no events to display.')
        return {}
    top = (df.groupby(['ticker','event_type'])['impact_short'].sum()
             .reset_index().sort_values('impact_short', ascending=False).head(20))
    payload = {'generated_at': datetime.datetime.utcnow().isoformat(),
               'top_events': top.to_dict(orient='records')}
    os.makedirs(OUT_DIR, exist_ok=True); os.makedirs(DASH_DIR, exist_ok=True)
    json_path = os.path.join(OUT_DIR, 'news_dashboard_latest.json')
    with open(json_path, 'w', encoding='utf-8') as f: json.dump(payload, f, indent=2, ensure_ascii=False)
    html_path = os.path.join(DASH_DIR, 'news_dashboard_latest.html')
    rows = '\n'.join(f"<tr><td>{r['ticker']}</td><td>{r['event_type']}</td><td>{r['impact_short']:.4f}</td></tr>" for r in payload['top_events'] if r.get('ticker'))
    html = f"""<html><head><meta charset='utf-8'><title>AION News Dashboard</title></head>
<body><h2>AION — News Intelligence Dashboard</h2>
<p>Generated at {payload['generated_at']} UTC</p>
<table border='1' cellpadding='6' cellspacing='0'>
<tr><th>Ticker</th><th>Event</th><th>Short Impact</th></tr>
{rows}
</table></body></html>"""
    with open(html_path, 'w', encoding='utf-8') as f: f.write(html)
    log(f'[dashboard_monitor] ✅ JSON → {json_path} | HTML → {html_path}')
    return {'json': json_path, 'html': html_path}

if __name__ == '__main__':
    build_daily_dashboard()
