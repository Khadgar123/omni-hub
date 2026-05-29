"""Market-regime committee (pure stdlib; no third-party deps, no LLM).

Fuses cheap, complementary members over ONE timeframe's bar series into a single
discrete regime label + an orthogonal change-point ``stand_down`` flag. Every
member reads causal features (``quant.features``), so the latest reading never
peeks at the future. The label is the gate the strategy layer obeys (trend
strategies fire only in trend regimes, mean-reversion only in ``range``).

Members (Phase-1 — thresholds are conventional **untuned defaults**; treat them
as hyper-parameters to fit under purged-CV later, not tuned numbers):
  * **ADX(14) strength** — >= ADX_TREND trending, >= ADX_STRONG strong, < ADX_RANGE range.
  * **EMA(50) slope / ATR** — scale-free direction; |slope/ATR| < SLOPE_EPS is flat.
  * **realized-vol bucket** — trailing-percentile low / normal / high (context overlay).

Change-point ``stand_down``: a one-sided CUSUM on standardized realized vol. It
trips on a sharp vol expansion (regime transition — where both trend and range
strategies bleed) and holds for a cooldown. ``stand_down`` is NOT a label; it is
an independent veto a higher layer can apply.

Label set (direction x strength): ``strong_down, down, range, up, strong_up``.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Sequence

from quant import features

# --- committee thresholds (conventional, UNTUNED defaults) -----------------
ADX_LEN = 14
ADX_RANGE = 20.0      # below => no directional strength => range
ADX_TREND = 25.0      # at/above => trending
ADX_STRONG = 40.0     # at/above => strong trend
EMA_LEN = 50
SLOPE_LOOKBACK = 10
ATR_LEN = 14
SLOPE_EPS = 0.05      # |EMA-slope per bar / ATR| below this is "flat"
VOL_LEN = 20
VOL_WINDOW = 180      # trailing window for the vol percentile bucket
# CUSUM change-point detector on standardized vol:
CUSUM_K = 0.5         # slack (drift allowance) in std units
CUSUM_H = 5.0         # decision threshold
CUSUM_COOLDOWN = 12   # bars to hold stand_down after a trip
CUSUM_WINDOW = 60     # trailing window for standardizing vol
VOL_FLOOR = 1e-6      # below this vol level there is nothing to call a "change"
SD_REL_FLOOR = 0.1    # floor the standardizer at 10% of the level (anti micro-noise)

TREND_REGIMES = frozenset({"up", "strong_up", "down", "strong_down"})
RANGE_REGIMES = frozenset({"range"})
LONG_BIAS_REGIMES = frozenset({"up", "strong_up"})
SHORT_BIAS_REGIMES = frozenset({"down", "strong_down"})


@dataclass(slots=True)
class RegimeResult:
    """One timeframe's regime reading (the latest, point-in-time bar)."""

    as_of: int                       # bucket_ts of the last bar (epoch micros, UTC)
    label: str                       # strong_down|down|range|up|strong_up
    direction: str                   # up|down|flat
    strength: str                    # strong|normal|weak
    stand_down: bool                 # change-point veto (orthogonal to label)
    adx: float | None
    slope_per_atr: float | None
    vol_bucket: str                  # low|normal|high
    n_bars: int
    insufficient: bool               # committee had too little data to form a view
    components: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _vol_bucket(vol_series: Sequence[float | None]) -> str:
    cur = features.last_valid(vol_series)
    if cur is None:
        return "normal"
    hist = [v for v in vol_series if v is not None]
    if len(hist) < 10:
        return "normal"
    below = sum(1 for v in hist if v <= cur)
    pct = below / len(hist)
    if pct <= 0.33:
        return "low"
    if pct >= 0.66:
        return "high"
    return "normal"


def cusum_standdown(
    vol_series: Sequence[float | None],
    *,
    k: float = CUSUM_K,
    h: float = CUSUM_H,
    cooldown: int = CUSUM_COOLDOWN,
    window: int = CUSUM_WINDOW,
) -> list[bool]:
    """Per-bar change-point flag via a one-sided CUSUM on standardized vol.

    Each bar's realized vol is standardized against its trailing ``window``;
    the CUSUM accumulates positive surprise and trips above ``h`` (then resets).
    A trip holds ``stand_down`` for ``cooldown`` bars.
    """
    out = [False] * len(vol_series)
    s = 0.0
    last_trip = -(10**9)
    for i, v in enumerate(vol_series):
        if v is None:
            out[i] = (i - last_trip) < cooldown
            continue
        hist = [x for x in vol_series[max(0, i - window):i] if x is not None]
        if len(hist) >= 10:
            mu = statistics.fmean(hist)
            if mu >= VOL_FLOOR:  # only meaningful when there IS volatility to change
                # floor the standardizer so a near-constant vol window can't turn
                # float noise into spurious z-scores, while a real jump still trips
                sd = max(statistics.pstdev(hist), SD_REL_FLOOR * mu)
                z = (v - mu) / sd
                s = max(0.0, s + z - k)
                if s > h:
                    last_trip = i
                    s = 0.0
        out[i] = (i - last_trip) < cooldown
    return out


def classify(bars: Sequence[dict]) -> RegimeResult:
    """Classify the regime as of the last bar in ``bars`` (sorted ascending)."""
    n = len(bars)
    as_of = int(bars[-1].get("bucket_ts", 0)) if n else 0
    closes = features.closes(bars)

    adx_v = features.last_valid(features.adx(bars, ADX_LEN)["adx"]) if n else None
    ema_series = features.ema(closes, EMA_LEN)
    slope_series = features.slope(ema_series, SLOPE_LOOKBACK)
    slope_v = features.last_valid(slope_series)
    atr_v = features.last_valid(features.atr(bars, ATR_LEN))
    vol_series = features.realized_vol(closes, VOL_LEN)
    vol_bucket = _vol_bucket(vol_series)
    stand_down = cusum_standdown(vol_series)[-1] if n else False

    slope_per_atr = (slope_v / atr_v) if (slope_v is not None and atr_v) else None

    # direction from scale-free EMA slope
    if slope_per_atr is None:
        direction = "flat"
    elif slope_per_atr > SLOPE_EPS:
        direction = "up"
    elif slope_per_atr < -SLOPE_EPS:
        direction = "down"
    else:
        direction = "flat"

    # strength from ADX
    if adx_v is None:
        strength = "weak"
    elif adx_v >= ADX_STRONG:
        strength = "strong"
    elif adx_v >= ADX_TREND:
        strength = "normal"
    else:
        strength = "weak"

    # fuse -> label
    if direction == "flat" or strength == "weak" or (adx_v is not None and adx_v < ADX_RANGE):
        label = "range"
    else:
        prefix = "strong_" if strength == "strong" else ""
        label = f"{prefix}{direction}"

    return RegimeResult(
        as_of=as_of,
        label=label,
        direction=direction,
        strength=strength,
        stand_down=bool(stand_down),
        adx=adx_v,
        slope_per_atr=slope_per_atr,
        vol_bucket=vol_bucket,
        n_bars=n,
        insufficient=(adx_v is None and slope_per_atr is None),
        components={
            "adx_thresholds": [ADX_RANGE, ADX_TREND, ADX_STRONG],
            "slope_eps": SLOPE_EPS,
            "ema_len": EMA_LEN,
        },
    )
