import json
from pathlib import Path
base = Path('/home/user/workspace/cron_tracking/ecb64bd3')
state_path = base / 'global_liquidity_tracker_state.json'
state = json.loads(state_path.read_text())
latest = state.get('latest', {})
latest['alerts'] = ['Stablecoin supply WoW >= $2B']
state['latest'] = latest
state['last_notification_date'] = '2026-08-28'
state_path.write_text(json.dumps(state, indent=2))
summary = {
    'fresh': False,
    'material': True,
    'asof_date': latest.get('asof_date'),
    'asof_prior_date': latest.get('asof_prior_date'),
    'alerts': latest['alerts'],
    'regime_score': latest.get('regime_score'),
    'regime_label': latest.get('regime_label'),
    'signal_notes': state.get('signal_notes', []),
    'failures': state.get('failures', {}),
    'notification_sent': True,
    'notification_reason': 'Stablecoin aggregate rose $2.596B WoW, crossing the $2B threshold.'
}
(base / 'run_summary.json').write_text(json.dumps(summary, indent=2))
findings = {
    'run_utc': state.get('last_run_utc'),
    'fresh_weekly_fred_data': False,
    'fred_status': 'All three split direct groups timed out, including one retry per failed group; saved FRED snapshot preserved.',
    'saved_fred_asof': latest.get('asof_date'),
    'regime': {'label': latest.get('regime_label'), 'score': latest.get('regime_score')},
    'saved_fed_net_liquidity_bil': latest.get('fed_net_liquidity_bil'),
    'saved_fed_net_liquidity_wow_bil': latest.get('fed_net_liquidity_wow_bil'),
    'saved_m2_bil': latest.get('M2_bil'),
    'stablecoins_total_usd': latest.get('stablecoins_total_usd'),
    'stablecoins_change_vs_saved_prior_usd': latest.get('stablecoins_wow_usd'),
    'stablecoins_change_vs_saved_prior_pct': latest.get('stablecoins_wow_pct'),
    'stablecoins_assets_count': latest.get('stablecoins_assets_count'),
    'saved_credit': {'ig_pct': latest.get('BAMLC0A0CM_pct'), 'hy_pct': latest.get('BAMLH0A0HYM2_pct'), 'nfci': latest.get('NFCI'), 'anfci': latest.get('ANFCI')},
    'saved_rates_funding': {'dgs2_pct': latest.get('DGS2_pct'), 'dgs10_pct': latest.get('DGS10_pct'), 't10y2y_pct': latest.get('T10Y2Y_pct'), 'sofr_pct': latest.get('SOFR_pct'), 'effr_pct': latest.get('EFFR_pct'), 'iorb_pct': latest.get('IORB_pct'), 'sofr_minus_iorb_bps': latest.get('SOFR_minus_IORB_bps'), 'sofr_minus_effr_bps': latest.get('SOFR_minus_EFFR_bps')},
    'saved_reserves_bil': latest.get('WRESBAL_bil'),
    'material_alerts': latest['alerts'],
    'notification_sent': True,
    'sources': list(state.get('sources', {}).values())
}
(base / 'findings_2026-08-28.json').write_text(json.dumps(findings, indent=2))
note = {
    'run_utc': state.get('last_run_utc'),
    'trigger': 'Friday 07:00 America/Chicago',
    'fred': {'status': 'unavailable', 'attempts': 2, 'groups': ['liquidity_m2', 'spreads_conditions', 'rates_funding'], 'action': 'Skipped after group-specific retries; preserved last complete FRED snapshot dated 2026-07-09'},
    'stablecoins': {'status': 'success', 'aggregate_usd': latest.get('stablecoins_total_usd'), 'assets_count': latest.get('stablecoins_assets_count'), 'change_vs_prior_saved_observation_usd': latest.get('stablecoins_wow_usd'), 'change_vs_prior_saved_observation_pct': latest.get('stablecoins_wow_pct'), 'threshold_event': True, 'action': 'In-app notification sent'},
    'ecb': {'status': 'success', 'latest_index_date': latest.get('ecb_wfs_date'), 'action': 'No newer index date than saved state'},
    'notification': 'sent in-app: stablecoin aggregate rose $2.596B WoW, crossing the $2B threshold'
}
(base / 'last_run_note.json').write_text(json.dumps(note, indent=2))
