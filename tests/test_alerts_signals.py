"""Threshold rules and regime scoring."""

from __future__ import annotations

import pytest

from liquidity_sentinel.alerts import evaluate, regime_alerts
from liquidity_sentinel.metrics import Snapshot
from liquidity_sentinel.signals import score_regime


@pytest.fixture
def thresholds(cfg):
    return cfg.alerts


def snap(**values) -> Snapshot:
    s = Snapshot()
    s.values.update(values)
    return s


def test_stablecoin_usd_threshold_fires(thresholds):
    alerts = evaluate(snap(stablecoins_wow_usd=2.6e9), thresholds)
    assert [a.key for a in alerts] == ["stablecoins_wow_usd"]
    assert "2.60B" in alerts[0].message


def test_stablecoin_alert_carries_value_and_threshold(thresholds):
    alert = evaluate(snap(stablecoins_wow_pct=0.012), thresholds)[0]
    assert alert.value == pytest.approx(0.012)
    assert alert.threshold == pytest.approx(0.01)


def test_below_threshold_is_silent(thresholds):
    assert evaluate(snap(stablecoins_wow_usd=1.0e9, stablecoins_wow_pct=0.002), thresholds) == []


def test_stablecoin_alerts_survive_total_fred_failure(thresholds):
    """The always-on signal must not be gated on FRED."""
    s = snap(stablecoins_wow_usd=3.0e9)
    s.stale_series = ["WALCL", "WRESBAL", "WTREGEN", "RRPONTSYD", "WM2NS"]
    assert [a.key for a in evaluate(s, thresholds)] == ["stablecoins_wow_usd"]


def test_stale_inputs_suppress_the_rules_that_depend_on_them(thresholds):
    """A frozen reading must not re-fire the same alert every week."""
    s = snap(WRESBAL_bil=2_900.0, WRESBAL_wow_bil=-120.0)
    s.stale_series = ["WRESBAL"]
    assert evaluate(s, thresholds) == []

    s.stale_series = []
    keys = {a.key for a in evaluate(s, thresholds)}
    assert {"reserves_floor", "reserves_drop", "WRESBAL_wow_bil"} <= keys


def test_net_liquidity_rule_needs_all_three_components_fresh(thresholds):
    s = snap(fed_net_liquidity_wow_bil=-150.0)
    s.stale_series = ["RRPONTSYD"]
    assert evaluate(s, thresholds) == []
    s.stale_series = []
    assert any(a.key == "fed_net_liquidity_wow_bil" for a in evaluate(s, thresholds))


def test_funding_spread_alerts_only_on_widening(thresholds):
    """Narrowing is not a stress signal; only widening is."""
    assert evaluate(snap(SOFR_minus_IORB_wow_bps=-11.0), thresholds) == []
    fired = evaluate(snap(SOFR_minus_IORB_wow_bps=8.0), thresholds)
    assert [a.key for a in fired] == ["SOFR_minus_IORB_wow_bps"]
    assert fired[0].severity == "critical"


def test_reserves_floor_is_a_level_rule_not_a_change_rule(thresholds):
    fired = evaluate(snap(WRESBAL_bil=2_924.9), thresholds)
    assert [a.key for a in fired] == ["reserves_floor"]
    assert fired[0].severity == "critical"


def test_regime_score_counts_only_available_signals():
    regime = score_regime(snap(fed_net_liquidity_wow_bil=50.0, M2_wow_bil=10.0))
    assert regime.score == 2
    assert regime.available == 2
    assert regime.label == "expanding"
    assert regime.confidence == "low", "two inputs is not a confident read"


def test_regime_confidence_rises_with_coverage():
    regime = score_regime(
        snap(
            fed_net_liquidity_wow_bil=50.0, M2_wow_bil=10.0, stablecoins_wow_usd=1e9,
            BAMLC0A0CM_wow_bps=-2.0, BAMLH0A0HYM2_wow_bps=-5.0, NFCI_wow=-0.01,
            ANFCI_wow=-0.01, WRESBAL_wow_bil=20.0, SOFR_minus_IORB_wow_bps=-1.0,
        )
    )
    assert regime.available == 9
    assert regime.score == 5, "clamped at +5"
    assert regime.confidence == "high"


def test_regime_score_is_clamped_both_ways():
    contracting = score_regime(
        snap(
            fed_net_liquidity_wow_bil=-50.0, M2_wow_bil=-10.0, stablecoins_wow_usd=-1e9,
            BAMLC0A0CM_wow_bps=2.0, BAMLH0A0HYM2_wow_bps=5.0, NFCI_wow=0.01,
            ANFCI_wow=0.01, WRESBAL_wow_bil=-20.0, SOFR_minus_IORB_wow_bps=3.0,
        )
    )
    assert contracting.score == -5
    assert contracting.label == "contracting"


def test_zero_change_is_not_a_vote():
    regime = score_regime(snap(fed_net_liquidity_wow_bil=0.0))
    assert regime.available == 0
    assert regime.label == "neutral"


def test_funding_falls_back_to_sofr_level_change():
    regime = score_regime(snap(SOFR_wow_bps=-4.0))
    assert regime.notes == ["+funding"]


def test_regime_alerts_compare_against_the_previous_run(thresholds):
    current = score_regime(snap(fed_net_liquidity_wow_bil=-50.0, M2_wow_bil=-10.0))
    fired = regime_alerts(current, {"regime_score": 3, "regime_label": "expanding"}, thresholds)
    keys = {a.key for a in fired}
    assert keys == {"regime_score_delta", "regime_label_change"}


def test_no_regime_alerts_without_a_previous_run(thresholds):
    current = score_regime(snap(fed_net_liquidity_wow_bil=50.0))
    assert regime_alerts(current, None, thresholds) == []
