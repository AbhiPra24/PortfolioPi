# Algorithm Layers

PortfolioPi uses a two-tiered system for analyzing the market: a tactical screener for short-term signals and a structural portfolio action classification engine for long-term holding guidance. These two systems operate independently and are intentionally separate, providing different lenses on the market rather than competing.

## 1. Tactical Screener (`algo/indicators.py`, `algo/screener.py`, `algo/ranking.py`)
The tactical screener is designed to identify short-term entry setups and short-term momentum shifts. It computes several classic technical indicators:

- **RSI (14)**: Measures short-term momentum (oversold < 30, overbought > 70).
- **MACD (12, 26, 9)**: Identifies momentum trend reversals. A positive histogram indicates bullish short-term momentum.
- **SMA 50 / 200 (Golden Cross)**: A structural filter where the 50-day SMA crossing above the 200-day SMA signals a long-term bullish trend.
- **52-Week High Proximity**: Checks how close the current price is to its 52-week high, capturing leading momentum stocks.
- **Volume Spikes**: Flags abnormal buying interest by comparing current volume to recent average volume.

These factors are combined using a weighted **composite_score** in `algo/ranking.py` to rank the most compelling tactical setups on a daily basis.

## 2. Portfolio Action Classification Engine
This structural engine answers the question: "What should I do with the stocks I currently hold or want to hold?" It issues stock-level guidance (BUY, HOLD, TRIM, SELL, SIP) based on leading, time-tested market methodologies.

- **Weinstein Stage Analysis** (`algo/stage_analysis.py`): Based on Stan Weinstein's "Secrets for Profiting in Bull and Bear Markets," this classifies a stock's structural trend into Stage 1 (Basing), Stage 2 (Advancing), Stage 3 (Topping), or Stage 4 (Declining).
- **Minervini Trend Template** (`algo/trend_template.py`): Derived from Mark Minervini's "Trade Like a Stock Market Wizard," this is a 7-point checklist ensuring a stock is in a confirmed, healthy structural uptrend before capital is committed.
- **Relative Strength** (`algo/relative_strength.py`): Inspired by William O'Neil's IBD RS Ratings ("How to Make Money in Stocks"), this calculates a weighted trailing outperformance score against the Nifty 50 index across 63, 126, 189, and 252-day windows.
- **Distribution Days** (`algo/distribution_days.py`): Another O'Neil concept tracking institutional selling pressure by counting down days (higher volume, lower close) over the last 25 trading sessions.
- **Chandelier Exit** (`algo/trailing_stop.py`): A volatility-based trailing stop-loss to protect profits without getting shaken out by normal noise.

These modules are synthesized in `algo/action_classifier.py` to produce actionable, human-readable portfolio guidance.

### ⚠️ Known Limitations
**Preliminary Backtest Constraints**: The backtest validating the Portfolio Action Classification Engine initially ran with only ~46 trading days of usable history (until the deeper backfill was completed). Consequently, the BUY-signal forward-return figures (-2.1% mean 20-day return in early tests) should be treated as preliminary and unvalidated until run across a statistically significant multi-year market cycle encompassing various regimes.
