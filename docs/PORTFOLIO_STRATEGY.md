# Portfolio Strategy, Rebalancing & Dividend Allocation Plan

> **Date Generated**: October 19, 2026  
> **Source Data**: Supabase PostgreSQL (`holdings_snapshot`, `ohlcv_cache`, `stock_actions`, `portfolio_value_history`)  
> **Frameworks**: Stan Weinstein Stage Analysis, Mark Minervini Trend Template, Finance-Guru Fail-Closed Guardrails, EigenLedger MPT Optimization, and Dividend Yield Analysis.

---

## 1. Portfolio Executive Summary

| Metric | Current Amount |
| :--- | :--- |
| **Total Invested Capital** | **₹8,63,354.19** |
| **Current Portfolio Value** | **₹9,28,980.55** |
| **Unrealized Gain / P&L** | **+₹65,626.36 (+7.60%)** |
| **Total Active Holdings** | **44 Assets** |
| **Top 5 Concentration** | **38.48%** |

---

## 2. Fail-Closed Guardrail Audit (Finance-Guru Model)

* **🚨 [DANGER] Single-Asset Concentration Breach (10% Limit)**:
  * **`GOLDEX`** (Gold ETF): ₹1,11,756.84 (**12.03%** of portfolio).
  * **`NIPSIL`** (Silver ETF): ₹1,08,690.40 (**11.70%** of portfolio).
* **⚠️ [WARNING] Sector Overweight (Precious Metals)**:
  * Total Gold & Silver ETF exposure (`GOLDEX` + `NIPSIL` + `HDFGOL`) accounts for **28.04%** (~₹2,60,500) of the entire portfolio with **0% dividend yield**.
* **⚠️ [WARNING] Holdings Fragmentation**:
  * 44 holdings creates unnecessary monitoring drag. 6 odd-lot positions are under ₹1,500 each and should be pruned.

---

## 3. Comprehensive Action Matrix (Growth, Risk & Dividends)

### A. 💰 WHICH TO ENCASH (Take Partial Profits & De-Risk)
*High-gain or overweight assets where capital must be trimmed to satisfy safety guardrails and redeployed into cash-generating assets.*

| Stock / ETF | Current Value | P&L | Dividend Yield | Beta ($\beta$) | Action & Rationale |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **`NIPSIL`** | ₹1,08,690 | **+29.8%** (+₹24.9k) | 0.0% | 1.32 | **Encash ~₹45,000–₹50,000**. High volatility drag; silver pays no cash flow. Trim below 6% weight. |
| **`GOLDEX`** | ₹1,11,756 | **+11.4%** (+₹11.4k) | 0.0% | 0.71 | **Encash ~₹25,000–₹30,000**. Lock in gains and bring under 10% guardrail cap. |
| **`FEDBAN`** | ₹38,173 | **+73.5%** (+₹16.1k) | 0.4% | 0.85 | **Book 50% Profit (~₹19,000)**. Lock in exceptional +73% run; price below Chandelier Exit. |
| **`MANAFI`** | ₹35,214 | **+55.0%** (+₹12.5k) | 1.3% | 1.29 | **Book 50% Profit (~₹17,500)**. Secure capital on trailing stop test. |

> **Total Liquidity Generated from Profit Booking:** **~₹1,06,000 - ₹1,16,000**

---

### B. ❌ WHICH TO SELL (Cut Dead Weight & Stage 4 Declines)
*Assets with negative structural momentum (Stage 4 declines) and low/zero dividends that are causing continuous capital destruction.*

| Stock | Current Value | P&L | Dividend Yield | Action & Rationale |
| :--- | :---: | :---: | :---: | :--- |
| **`SUZENE`** | ₹15,336 | **-19.7%** (-₹3.7k) | 0.0% | **Full Exit (100%)**. Zero yield; price breached ₹43 stop loss; trend broken. |
| **`INDOIL`** | ₹19,760 | **-3.2%** (-₹653) | 2.5% | **Full Exit (100%)**. Stage 4 decline; better yields available in InvITs/REITs. |
| **`BANBAR`** | ₹11,492 | **-10.1%** (-₹1.3k) | 3.5% | **Full Exit (100%)**. Confirmed Stage 4 decline; banking cycle topping. |
| **`XCHSOL`** | ₹6,042 | **-35.5%** (-₹3.3k) | 0.0% | **Full Exit (100%)**. Stage 3/4 distribution; severe erosion. |
| **`SWILIM`** | ₹4,182 | **-29.6%** (-₹1.7k) | 0.0% | **Full Exit (100%)**. Zero yield; continuous breakdown. |
| **`HTMGLO`** | ₹726 | **-72.8%** (-₹1.9k) | 0.0% | **Full Exit (100%)**. Dead asset; tax-loss harvest. |
| **`DABIND`** | ₹383 | **-33.2%** (-₹190) | 0.6% | **Full Exit (100%)**. Negligible holding; clean up. |
| **Odd-Lot Dust** | ~₹2,940 | Various | — | **Full Exit**: `IRCINT`, `REL150`, `INDR`, `ICI500`, `TRAREA`, `ALLTER`. |

> **Total Liquidity Generated from Exits:** **~₹60,000 - ₹62,000**

---

### C. 💎 HIGH-YIELD DIVIDEND ANCHORS: DO NOT SELL (Hold / Accumulate)
*Do not sell based purely on technical consolidation. These assets act as defensive cash-flow generators and reduce overall portfolio volatility.*

| Stock / Asset | Current Value | Dividend / Distribution Yield | Est. Annual Income | Strategic Role |
| :--- | :---: | :---: | :---: | :--- |
| **`INDGRI`** (India Grid InvIT) | ₹44,774 | **~9.8% – 10.2%** | **~₹4,400 / yr** | **Core High-Yield Anchor**. Sovereign-backed power transmission cash flow. |
| **`EMBOFF`** (Embassy REIT) | ₹44,400 | **~6.8% – 7.2%** | **~₹3,100 / yr** | **Core REIT Anchor**. Grade-A office tech parks; low beta ($\beta = 0.24$). |
| **`NEXSEL`** (Nexus Select REIT) | ₹35,566 | **~6.5% – 7.0%** | **~₹2,350 / yr** | **Consumption Core**. Top retail malls; consistent quarterly NDCF. |
| **`COALIN`** (Coal India) | ₹29,844 | **~6.5%** (₹26.50/sh) | **~₹1,908 / yr** | **Cash Machine**. Exceptional state dividend payout. |
| **`ONGC`** | ₹20,856 | **~6.5%** (₹14.50/sh) | **~₹1,363 / yr** | **Sovereign Yield Anchor**. High dividend cover. |
| **`POWINF` / `POWGRI`** | ₹47,858 | **~3.6% – 4.0%** (₹9.00/sh) | **~₹1,750 / yr** | **Defensive Utility**. Low beta ($\beta = 0.08$). |
| **`ITC`** | ₹3,985 | **~6.1%** (₹16.00/sh) | **~₹240 / yr** | **Dividend Support**. FMCG defensiveness. |

> **Current Base Dividend Income**: **~₹15,100 / year**

---

### D. 🟢 WHICH TO BUY / ACCUMULATE
*Reinvest the ~₹1,65,000–₹1,75,000 unlocked liquidity across Stage 2 momentum leaders and high-yield infrastructure ballast.*

#### 1. Stage 2 Momentum Leaders (Capital Appreciation)
* **`BHEL`**:
  * **P&L**: +49.7% | **RS Rating**: 0.43 | **Trend Template**: 7/7 passing.
  * **Allocation**: Add **~₹25,000** on pullbacks.
* **`HIMCHE`** (Himadri Speciality Chemical):
  * **P&L**: +24.3% | **RS Rating**: 0.39 | **Trend Template**: 6/7 passing.
  * **Allocation**: Add **~₹15,000** via SIP / staggered buy.

#### 2. Institutional Yield Ballast (EigenLedger MPT Optimization)
* **`INDGRI`** (India Grid InvIT):
  * Rebalancing model recommends raising target to **8–10%** weight.
  * **Allocation**: Add **~₹40,000** (yields **~₹4,000/yr** incremental cash).
* **`EMBOFF`** (Embassy REIT):
  * Rebalancing model recommends raising target to **7–9%** weight.
  * **Allocation**: Add **~₹30,000** (yields **~₹2,100/yr** incremental cash).
* **`NEXSEL`** (Nexus Select REIT):
  * Rebalancing model recommends raising target to **6–8%** weight.
  * **Allocation**: Add **~₹25,000** (yields **~₹1,700/yr** incremental cash).
* **`COALIN`** (Coal India):
  * **Allocation**: Add **~₹15,000** on dips below ₹400 (yields **~₹1,000/yr** incremental cash).
* **Cash Buffer / Liquid ETF (`LIQUIDBEES`)**:
  * Keep **~₹15,000 – ₹20,000** ready for market correction opportunities.

---

## 4. Rebalanced Portfolio Outcome

| Metric | Current State | Rebalanced Target |
| :--- | :---: | :---: |
| **Total Holdings Count** | 44 (over-fragmented) | **~25 (institutional discipline)** |
| **Precious Metals Exposure** | 28.0% (overweight risk) | **15.0% (balanced macro hedge)** |
| **REITs & InvITs Exposure** | 13.4% | **24.0% (high regular cash flow)** |
| **Portfolio Beta ($\beta$)** | ~0.82 (dragged by silver) | **~0.58 (defensive, shock-resistant)** |
| **Annual Dividend / Cash Yield** | **~₹15,100 / yr** | **~₹24,000 – ₹25,500 / yr (+65% boost)** |
| **Guardrail Compliance** | 2 Violations | **100% Guardrails Passed** |
