"""真实数据库周期吸筹 Top10 分析。

候选和持币序列来自 select.db；PnL、Meta、S1-S5 仅作为解释字段。
所有身份匹配均使用 ``chain + token_address``，禁止按 symbol 关联。
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Sequence


HARD_QUALITY_FLAGS = {
    "NO_ACC_SNAPSHOT",
    "LOW_COVERAGE",
    "NO_RHO",
    "NO_TOKEN_HISTORY",
    "HISTORY_MARKET_MISSING",
    "NO_LIVE_MARKET",
    "FAKE_LIQ_ALERT",
}


def _identity(chain: str, address: str) -> tuple[str, str]:
    return ((chain or "").strip().lower(), (address or "").strip().lower())


def _readonly_connection(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _pearson(values: Sequence[float]) -> Optional[float]:
    if len(values) < 3:
        return None
    xs = list(range(len(values)))
    mx = sum(xs) / len(xs)
    my = sum(values) / len(values)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    x_var = sum((x - mx) ** 2 for x in xs)
    y_var = sum((y - my) ** 2 for y in values)
    if x_var <= 0 or y_var <= 0:
        return None
    return numerator / math.sqrt(x_var * y_var)


@dataclass
class AccumulationSeriesPoint:
    date: str
    holding_amount: float


@dataclass
class AccumulationResult:
    run_id: str
    rank: Optional[int]
    token_symbol: str
    chain: str
    token_address: str
    best_snapshot_time: Optional[str]
    effective_days: int
    acc_count: int
    latest_hold_amount: Optional[float]
    rho_60d: Optional[float]
    change_7d: Optional[float]
    change_60d: Optional[float]
    max_daily_change: Optional[float]
    max_daily_change_date: Optional[str]
    pnl_ratio: Optional[float]
    price_now_ret: Optional[float]
    meta_score: Optional[float]
    stage: Optional[str]
    quality_status: str
    risk_flags: List[str] = field(default_factory=list)
    series: List[AccumulationSeriesPoint] = field(default_factory=list)
    surge_result: object = None

    @property
    def identity(self) -> tuple[str, str]:
        return _identity(self.chain, self.token_address)


@dataclass
class AccumulationAnalysisRun:
    run_id: str
    generated_at: str
    candidate_count: int
    quality_pass_count: int
    top10: List[AccumulationResult]
    risk_rows: List[AccumulationResult]


class AccumulationTop10Analyzer:
    """先计算全库吸筹证据，再质量门禁并排序 Top10。"""

    def __init__(
        self,
        select_db_path: str = "/opt/select-coin/data/select.db",
        sum_db_path: str = "/opt/AI-SUM/select-sum.db",
        result_db_path: Optional[str] = None,
        freshness_hours: int = 48,
        history_days: int = 60,
        min_effective_days: int = 14,
    ):
        self.select_db_path = select_db_path
        self.sum_db_path = sum_db_path
        self.result_db_path = result_db_path or sum_db_path
        self.freshness_hours = freshness_hours
        self.history_days = history_days
        self.min_effective_days = min_effective_days

    def analyze(
        self,
        surge_results: Optional[Iterable[object]] = None,
        fake_liq_results: Optional[Iterable[dict]] = None,
        persist: bool = True,
        run_id: Optional[str] = None,
        candidate_offset: int = 0,
        candidate_limit: Optional[int] = 15,
    ) -> AccumulationAnalysisRun:
        if not os.path.isfile(self.select_db_path):
            raise FileNotFoundError(self.select_db_path)
        if not os.path.isfile(self.sum_db_path):
            raise FileNotFoundError(self.sum_db_path)

        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        run_id = run_id or datetime.now().strftime("%Y%m%dT%H%M%S%f")
        surge_map = self._surge_map(surge_results or [])
        fake_keys, fake_addresses = self._fake_keys(fake_liq_results or [])

        with _readonly_connection(self.select_db_path) as select_conn, _readonly_connection(self.sum_db_path) as sum_conn:
            candidates = select_conn.execute(
                """
                SELECT chain, token_address, symbol, bm_last_snapshot
                FROM token_names
                WHERE bm_last_snapshot >= datetime('now', ?)
                  AND bm_acc_count > 0
                ORDER BY bm_acc_count DESC
                LIMIT 40
                """,
                (f"-{self.freshness_hours} hours",),
            ).fetchall()
            unique_candidates = {}
            for candidate in candidates:
                unique_candidates.setdefault(
                    _identity(candidate["chain"], candidate["token_address"]),
                    candidate,
                )
            candidates = list(unique_candidates.values())
            if candidate_offset or candidate_limit is not None:
                stop = None if candidate_limit is None else candidate_offset + candidate_limit
                candidates = candidates[candidate_offset:stop]
            latest_meta_scan = sum_conn.execute(
                "SELECT MAX(scan_time) FROM meta_snapshots"
            ).fetchone()[0]

            results = [
                self._analyze_candidate(
                    select_conn,
                    sum_conn,
                    row,
                    run_id,
                    latest_meta_scan,
                    surge_map,
                    fake_keys,
                    fake_addresses,
                )
                for row in candidates
            ]

        quality_rows = [
            row for row in results
            if row.quality_status == "PASS" and row.rho_60d is not None
        ]
        quality_rows.sort(
            key=lambda row: (
                row.rho_60d,
                row.change_7d if row.change_7d is not None else float("-inf"),
                row.acc_count,
            ),
            reverse=True,
        )
        top10 = quality_rows[:10]
        for rank, row in enumerate(top10, 1):
            row.rank = rank

        risk_rows = [row for row in results if row.quality_status != "PASS"]
        risk_rows.sort(
            key=lambda row: (
                row.rho_60d if row.rho_60d is not None else float("-inf"),
                row.change_7d if row.change_7d is not None else float("-inf"),
                row.acc_count,
            ),
            reverse=True,
        )

        analysis_run = AccumulationAnalysisRun(
            run_id=run_id,
            generated_at=generated_at,
            candidate_count=len(results),
            quality_pass_count=len(quality_rows),
            top10=top10,
            risk_rows=risk_rows[:10],
        )
        if persist:
            self._persist(analysis_run)
        return analysis_run

    @staticmethod
    def _surge_map(results: Iterable[object]) -> dict[tuple[str, str], object]:
        mapping = {}
        by_address = {}
        ambiguous_addresses = set()
        for row in results:
            key = _identity(
                getattr(row, "chain", ""),
                getattr(row, "token_address", ""),
            )
            if key[0] and key[1]:
                mapping[key] = row
            if key[1]:
                if key[1] in by_address:
                    ambiguous_addresses.add(key[1])
                else:
                    by_address[key[1]] = row
        # token_history 的历史 chain 为空时旧分析器会默认 bsc；仅地址全局唯一时兼容。
        for address, row in by_address.items():
            if address not in ambiguous_addresses:
                mapping[("", address)] = row
        return mapping

    @staticmethod
    def _fake_keys(results: Iterable[dict]) -> tuple[set[tuple[str, str]], set[str]]:
        keys: set[tuple[str, str]] = set()
        addresses: set[str] = set()
        for row in results:
            if row.get("status") != "FAKE_ZERO_LIQUIDITY":
                continue
            address = str(row.get("token") or row.get("token_address") or "").lower()
            chain = str(row.get("chain") or "").lower()
            if address:
                addresses.add(address)
                if chain:
                    keys.add((chain, address))
        return keys, addresses

    def _fallback_surge(self, address: str, chain: str, symbol: str):
        try:
            from anomaly_watch.impulse_surge_analyzer import ImpulseSurgeAnalyzer
            analyzer = ImpulseSurgeAnalyzer(db_path=self.sum_db_path)
            return analyzer.analyze_single(address, chain, symbol)
        except Exception:
            return None

    def _analyze_candidate(
        self,
        select_conn: sqlite3.Connection,
        sum_conn: sqlite3.Connection,
        token: sqlite3.Row,
        run_id: str,
        latest_meta_scan: Optional[str],
        surge_map: dict,
        fake_keys: set,
        fake_addresses: set,
    ) -> AccumulationResult:
        chain = (token["chain"] or "").strip()
        address = (token["token_address"] or "").strip()
        symbol = token["symbol"] or "UNKNOWN"
        latest_snapshot = token["bm_last_snapshot"]
        flags: List[str] = []

        best = select_conn.execute(
            """
            SELECT snapshot_time, COUNT(DISTINCT wallet_address) AS acc_count
            FROM bubblemap_holders
            WHERE chain = ? AND token_address = ?
              AND snapshot_time LIKE substr(?, 1, 10) || '%' AND is_accumulating = 1
            GROUP BY snapshot_time
            ORDER BY acc_count DESC, snapshot_time DESC
            LIMIT 1
            """,
            (chain, address, latest_snapshot),
        ).fetchone()

        series: List[AccumulationSeriesPoint] = []
        wallets: List[str] = []
        best_snapshot_time = best["snapshot_time"] if best else None
        if best:
            wallets = [
                row[0]
                for row in select_conn.execute(
                    """
                    SELECT DISTINCT wallet_address
                    FROM bubblemap_holders
                    WHERE chain = ? AND token_address = ?
                      AND snapshot_time = ? AND is_accumulating = 1
                    """,
                    (chain, address, best_snapshot_time),
                )
            ]
        else:
            flags.append("NO_ACC_SNAPSHOT")

        if wallets:
            placeholders = ",".join("?" for _ in wallets)
            start_time = (
                datetime.fromisoformat(best_snapshot_time) - timedelta(days=self.history_days)
            ).strftime("%Y-%m-%d %H:%M:%S")
            rows = select_conn.execute(
                f"""
                WITH daily_deduped AS (
                    SELECT wallet_address, snapshot_time, hold_amount,
                           ROW_NUMBER() OVER (
                               PARTITION BY wallet_address, DATE(snapshot_time)
                               ORDER BY snapshot_time DESC
                           ) AS rn
                    FROM bubblemap_holders
                    WHERE chain = ? AND token_address = ?
                      AND snapshot_time >= ?
                      AND wallet_address IN ({placeholders})
                )
                SELECT DATE(snapshot_time) AS day, SUM(hold_amount) AS holding_amount
                FROM daily_deduped
                WHERE rn = 1
                GROUP BY DATE(snapshot_time)
                ORDER BY day
                """,
                [chain, address, start_time, *wallets],
            ).fetchall()
            series = [
                AccumulationSeriesPoint(row["day"], float(row["holding_amount"] or 0))
                for row in rows
            ]

        if len(series) < self.min_effective_days:
            flags.append("LOW_COVERAGE")
        values = [point.holding_amount for point in series]
        rho = _pearson(values)
        if rho is None:
            flags.append("NO_RHO")

        change_60d = self._change(values[0], values[-1]) if len(values) > 1 else None
        change_7d = self._seven_day_change(series)
        max_daily_change, max_daily_change_date = self._max_daily_change(series)

        identity_chain, identity_address = _identity(chain, address)
        history = sum_conn.execute(
            """
            SELECT computed_date, pnl_ratio, price_now_ret, volume_24h, reserve_usd
            FROM token_history
            WHERE LOWER(token_address) = ?
              AND (COALESCE(chain, '') = '' OR LOWER(chain) = ?)
            ORDER BY CASE WHEN LOWER(chain) = ? THEN 0 ELSE 1 END,
                     computed_date DESC
            LIMIT 1
            """,
            (identity_address, identity_chain, identity_chain),
        ).fetchone()

        max_history_market = sum_conn.execute(
            """
            SELECT MAX(volume_24h) as max_vol, MAX(reserve_usd) as max_res
            FROM token_history
            WHERE LOWER(token_address) = ?
              AND computed_date >= date('now', '-7 days')
            """,
            (identity_address,),
        ).fetchone()

        if not history:
            flags.append("NO_TOKEN_HISTORY")
        else:
            vol_check = max(float(history["volume_24h"] or 0), float(max_history_market["max_vol"] or 0) if max_history_market else 0)
            res_check = max(float(history["reserve_usd"] or 0), float(max_history_market["max_res"] or 0) if max_history_market else 0)
            if vol_check <= 0 or res_check <= 0:
                flags.append("HISTORY_MARKET_MISSING")

        market = select_conn.execute(
            """
            SELECT reserve_usd, volume_24h
            FROM gecko_market_data
            WHERE chain = ? AND token_address = ?
            ORDER BY scan_time DESC
            LIMIT 1
            """,
            (chain, address),
        ).fetchone()
        if not market or float(market["reserve_usd"] or 0) <= 0 or float(market["volume_24h"] or 0) <= 0:
            flags.append("NO_LIVE_MARKET")

        # 跨库回溯 select.db 的 gecko_market_data 原原生表 (动态消除落盘污染引发的误杀)
        gecko_live = select_conn.execute(
            """
            SELECT reserve_usd, volume_24h
            FROM gecko_market_data
            WHERE LOWER(token_address) = ?
              AND scan_time >= datetime('now', '-7 days')
            ORDER BY scan_time DESC
            LIMIT 1
            """,
            (identity_address,),
        ).fetchone()

        if gecko_live:
            g_res = float(gecko_live["reserve_usd"] or 0)
            g_vol = float(gecko_live["volume_24h"] or 0)
            if g_res > 10000 and g_vol > 0:
                flags = [f for f in flags if f not in ("HISTORY_MARKET_MISSING", "NO_TOKEN_HISTORY")]
            elif g_vol <= 0:
                if "NO_LIVE_MARKET" not in flags:
                    flags.append("NO_LIVE_MARKET")

        if (identity_chain, identity_address) in fake_keys or identity_address in fake_addresses:
            flags.append("FAKE_LIQ_ALERT")

        meta = None
        if latest_meta_scan:
            meta = sum_conn.execute(
                """
                SELECT meta_score, stage
                FROM meta_snapshots
                WHERE scan_time = ? AND LOWER(chain) = ? AND LOWER(token_address) = ?
                LIMIT 1
                """,
                (latest_meta_scan, identity_chain, identity_address),
            ).fetchone()

        quality_status = "FAIL" if any(flag in HARD_QUALITY_FLAGS for flag in flags) else "PASS"
        return AccumulationResult(
            run_id=run_id,
            rank=None,
            token_symbol=symbol,
            chain=chain,
            token_address=address,
            best_snapshot_time=best_snapshot_time,
            effective_days=len(series),
            acc_count=len(wallets),
            latest_hold_amount=values[-1] if values else None,
            rho_60d=rho,
            change_7d=change_7d,
            change_60d=change_60d,
            max_daily_change=max_daily_change,
            max_daily_change_date=max_daily_change_date,
            pnl_ratio=float(history["pnl_ratio"]) if history and history["pnl_ratio"] is not None else None,
            price_now_ret=float(history["price_now_ret"]) if history and history["price_now_ret"] is not None else None,
            meta_score=float(meta["meta_score"]) if meta and meta["meta_score"] is not None else None,
            stage=meta["stage"] if meta else None,
            quality_status=quality_status,
            risk_flags=flags,
            series=series,
            surge_result=(
                surge_map.get(_identity(chain, address))
                or surge_map.get(("", _identity(chain, address)[1]))
                or self._fallback_surge(address, chain, symbol)
            ),
        )

    @staticmethod
    def _change(old: float, new: float) -> Optional[float]:
        if not old:
            return None
        return (new - old) / abs(old)

    def _seven_day_change(self, series: Sequence[AccumulationSeriesPoint]) -> Optional[float]:
        if not series:
            return None
        target = datetime.fromisoformat(series[-1].date).date() - timedelta(days=7)
        prior = None
        for point in series:
            if datetime.fromisoformat(point.date).date() <= target:
                prior = point.holding_amount
        return self._change(prior, series[-1].holding_amount) if prior is not None else None

    def _max_daily_change(
        self, series: Sequence[AccumulationSeriesPoint]
    ) -> tuple[Optional[float], Optional[str]]:
        changes = []
        for previous, current in zip(series, series[1:]):
            change = self._change(previous.holding_amount, current.holding_amount)
            if change is not None:
                changes.append((change, current.date))
        return max(changes, default=(None, None), key=lambda item: item[0] if item[0] is not None else -math.inf)

    def _persist(self, analysis_run: AccumulationAnalysisRun) -> None:
        with sqlite3.connect(self.result_db_path, timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS impulse_accumulation_snapshots (
                    run_id TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    rank INTEGER,
                    chain TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    token_symbol TEXT NOT NULL,
                    best_snapshot_time TEXT,
                    effective_days INTEGER NOT NULL,
                    acc_count INTEGER NOT NULL,
                    latest_hold_amount REAL,
                    rho_60d REAL,
                    change_7d REAL,
                    change_60d REAL,
                    max_daily_change REAL,
                    max_daily_change_date TEXT,
                    pnl_ratio REAL,
                    price_now_ret REAL,
                    meta_score REAL,
                    stage TEXT,
                    quality_status TEXT NOT NULL,
                    risk_flags_json TEXT NOT NULL,
                    trigger_reasons_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, chain, token_address)
                );
                CREATE TABLE IF NOT EXISTS impulse_accumulation_series (
                    run_id TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    series_date TEXT NOT NULL,
                    holding_amount REAL NOT NULL,
                    PRIMARY KEY (run_id, chain, token_address, series_date)
                );
                CREATE INDEX IF NOT EXISTS idx_impulse_acc_identity
                    ON impulse_accumulation_snapshots(chain, token_address, generated_at DESC);
                """
            )
            persisted_rows = [*analysis_run.top10, *analysis_run.risk_rows]
            for row in persisted_rows:
                reasons = list(getattr(row.surge_result, "trigger_reasons", []) or [])
                conn.execute(
                    """
                    INSERT OR REPLACE INTO impulse_accumulation_snapshots (
                        run_id, generated_at, rank, chain, token_address, token_symbol,
                        best_snapshot_time, effective_days, acc_count, latest_hold_amount,
                        rho_60d, change_7d, change_60d, max_daily_change,
                        max_daily_change_date, pnl_ratio, price_now_ret, meta_score, stage,
                        quality_status, risk_flags_json, trigger_reasons_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.run_id,
                        analysis_run.generated_at,
                        row.rank,
                        row.chain,
                        row.token_address,
                        row.token_symbol,
                        row.best_snapshot_time,
                        row.effective_days,
                        row.acc_count,
                        row.latest_hold_amount,
                        row.rho_60d,
                        row.change_7d,
                        row.change_60d,
                        row.max_daily_change,
                        row.max_daily_change_date,
                        row.pnl_ratio,
                        row.price_now_ret,
                        row.meta_score,
                        row.stage,
                        row.quality_status,
                        json.dumps(row.risk_flags, ensure_ascii=False),
                        json.dumps(reasons, ensure_ascii=False),
                    ),
                )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO impulse_accumulation_series
                    (run_id, chain, token_address, series_date, holding_amount)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (row.run_id, row.chain, row.token_address, point.date, point.holding_amount)
                        for point in row.series
                    ],
                )
            conn.commit()

