"""HyperLiquid SDK wrapper: positions, sizing, market orders, SL/TP."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger("trader")


def api_url(network: str) -> str:
    if network == "mainnet":
        return constants.MAINNET_API_URL
    return constants.TESTNET_API_URL


def _round_size(size: float, sz_decimals: int) -> float:
    if sz_decimals <= 0:
        return math.floor(size)
    factor = 10 ** sz_decimals
    return math.floor(size * factor) / factor


UNIFIED_ACCOUNT_MODES = {"unifiedAccount", "portfolioMargin"}
PERP_MAX_DECIMALS = 6
TRIGGER_LIMIT_SLIPPAGE = 0.08
CORE_MARKET_SLIPPAGE = 0.02
HIP3_MARKET_SLIPPAGE = 0.10


def market_slippage(coin: str) -> float:
    """HIP-3 books (xyz:TSLA) are thin — allow more IOC slippage than core perps."""
    return HIP3_MARKET_SLIPPAGE if ":" in coin else CORE_MARKET_SLIPPAGE


def _round_px(px: float, sz_decimals: int) -> float:
    """Match HyperLiquid tick rules so TP/SL is not rejected as 'not divisible by tick size'.

    Perps: at most 5 significant figures, and at most (6 - szDecimals) decimal places.
    Integer prices above 100k are always valid. See hyperliquid-python-sdk examples/rounding.py.
    """
    if px <= 0:
        return 0.0
    if px > 100_000:
        return float(round(px))
    decimals = max(0, PERP_MAX_DECIMALS - int(sz_decimals))
    return float(round(float(f"{px:.5g}"), decimals))


def fill_avg_px(raw: Any) -> float | None:
    try:
        statuses = (((raw or {}).get("response") or {}).get("data") or {}).get("statuses") or []
        for status in statuses:
            filled = (status or {}).get("filled") or {}
            px = filled.get("avgPx")
            if px not in (None, ""):
                return float(px)
    except (TypeError, ValueError):
        return None
    return None


def trigger_already_active(is_long: bool, tpsl: str, trigger_px: float, mark_px: float) -> bool:
    """True if this TP/SL is already in the money and would close the new position."""
    if tpsl == "sl":
        return mark_px <= trigger_px if is_long else mark_px >= trigger_px
    return mark_px >= trigger_px if is_long else mark_px <= trigger_px


def prepare_triggers(
    is_long: bool,
    entry_px: float,
    stop_loss: float | None,
    take_profit: float | None,
) -> tuple[float | None, float | None, list[str], str | None]:
    """Assign SL/TP for this side, or return an error if they cannot form a valid band.

    The lower alert price is the downside of the range, the higher is the upside.
    Long: SL = lower, TP = higher. Short: switched (SL = higher, TP = lower).
    """
    notes: list[str] = []
    if stop_loss is None and take_profit is None:
        return None, None, notes, None

    sl, tp = stop_loss, take_profit
    if sl is not None and tp is not None:
        if sl == tp:
            return None, None, notes, f"SL and TP are equal ({sl}) vs entry {entry_px}"
        low, high = (sl, tp) if sl < tp else (tp, sl)
        if not (low < entry_px < high):
            side = "long" if is_long else "short"
            return (
                None,
                None,
                notes,
                f"SL/TP {low}/{high} outside range vs entry {entry_px} ({side})",
            )
        sl, tp = (low, high) if is_long else (high, low)
        if sl != stop_loss or tp != take_profit:
            notes.append(f"SL/TP assigned: SL {sl} TP {tp}")
    else:
        if sl is not None and trigger_already_active(is_long, "sl", sl, entry_px):
            need = "below" if is_long else "above"
            return None, None, notes, f"SL {sl} is on the wrong side of entry {entry_px} (needs to be {need})"
        if tp is not None and trigger_already_active(is_long, "tp", tp, entry_px):
            need = "above" if is_long else "below"
            return None, None, notes, f"TP {tp} is on the wrong side of entry {entry_px} (needs to be {need})"

    if sl is not None and trigger_already_active(is_long, "sl", sl, entry_px):
        return None, None, notes, f"SL {sl} would fire immediately vs entry {entry_px}"
    if tp is not None and trigger_already_active(is_long, "tp", tp, entry_px):
        return None, None, notes, f"TP {tp} would fire immediately vs entry {entry_px}"
    return sl, tp, notes, None


def assign_sl_tp(
    is_long: bool,
    entry_px: float,
    stop_loss: float | None,
    take_profit: float | None,
) -> tuple[float | None, float | None, list[str]]:
    sl, tp, notes, _err = prepare_triggers(is_long, entry_px, stop_loss, take_profit)
    return sl, tp, notes


def exchange_status_error(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return f"Unexpected exchange response: {raw}"
    if raw.get("status") != "ok":
        return str(raw.get("response") or raw)
    statuses = (((raw.get("response") or {}).get("data") or {}).get("statuses") or [])
    errors = [str(item.get("error")) for item in statuses if isinstance(item, dict) and item.get("error")]
    if errors:
        return "; ".join(errors)
    return None


def trigger_limit_px(close_is_buy: bool, trigger_px: float, sz_decimals: int) -> float:
    """Limit once the trigger fires — slightly worse than trigger so a market TP/SL can fill."""
    slipped = trigger_px * (1 + TRIGGER_LIMIT_SLIPPAGE) if close_is_buy else trigger_px * (1 - TRIGGER_LIMIT_SLIPPAGE)
    return _round_px(max(slipped, 0.0), sz_decimals)


def _spot_usdc_available(spot_state: dict[str, Any] | None) -> float:
    for item in (spot_state or {}).get("balances") or []:
        if str(item.get("coin") or "").upper() != "USDC":
            continue
        total = float(item.get("total") or 0)
        hold = float(item.get("hold") or 0)
        return max(0.0, total - hold)
    return 0.0


def _perp_account_value(perp_state: dict[str, Any] | None) -> float:
    summary = (perp_state or {}).get("marginSummary") or {}
    return float(summary.get("accountValue") or 0)


def trading_account_value(
    perp_state: dict[str, Any] | None,
    spot_state: dict[str, Any] | None,
    abstraction: str | None,
) -> float:
    """Collateral used for perp sizing.

    Unified / portfolio margin keep USDC in spotClearinghouseState.
    Classic (manual) accounts keep perp collateral in clearinghouseState.
    """
    perp = _perp_account_value(perp_state)
    if abstraction in UNIFIED_ACCOUNT_MODES:
        return max(perp, _spot_usdc_available(spot_state))
    return perp


def _float_or_none(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _spot_mark_px(coin: str, token: Any, mids: dict[str, float]) -> float | None:
    if str(coin).upper() == "USDC":
        return 1.0
    keys = [str(coin), f"{coin}/USDC"]
    if token not in (None, ""):
        keys.extend([f"@{token}", f"@{token}/USDC"])
    for key in keys:
        if key in mids:
            return mids[key]
    upper = {str(k).upper(): v for k, v in mids.items()}
    for key in keys:
        found = upper.get(key.upper())
        if found is not None:
            return found
    return None


def spot_assets_from_state(
    spot_state: dict[str, Any] | None,
    mids: dict[str, float],
) -> list[dict[str, Any]]:
    """Spot holdings with current USD value (mark, or entry notional if no mid)."""
    assets: list[dict[str, Any]] = []
    for item in (spot_state or {}).get("balances") or []:
        total = float(item.get("total") or 0)
        if total == 0:
            continue
        coin = str(item.get("coin") or "")
        token = item.get("token")
        mark = _spot_mark_px(coin, token, mids)
        entry_ntl = _float_or_none(item.get("entryNtl"))
        value = (total * mark) if mark is not None else entry_ntl
        assets.append(
            {
                "coin": coin,
                "size": total,
                "hold": float(item.get("hold") or 0),
                "mark_px": mark,
                "value": value,
            }
        )
    return assets


def wallet_nav(
    perp_value: float,
    assets: list[dict[str, Any]],
    abstraction: str | None,
) -> float:
    """Wallet equity: all spot assets at mark plus perp equity, without double-counting unified USDC.

    Unified accounts lock USDC as perp margin (`hold`). That cash is already in
    `perp_value` together with unrealized PnL, so it is removed from spot first.
    Portfolio margin may already fold every asset into `accountValue`.
    """
    spot_total = 0.0
    usdc_hold = 0.0
    for asset in assets:
        value = float(asset.get("value") or 0)
        spot_total += value
        if str(asset.get("coin") or "").upper() == "USDC":
            usdc_hold += float(asset.get("hold") or 0)
    if abstraction == "portfolioMargin":
        return max(perp_value, spot_total)
    if abstraction in UNIFIED_ACCOUNT_MODES:
        return spot_total - usdc_hold + perp_value
    return perp_value + spot_total


def extra_perp_dexes(coins: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    """Builder dex prefixes from HIP-3 names like xyz:TSLA."""
    out: list[str] = []
    seen: set[str] = set()
    for name in coins:
        if not name or ":" not in name:
            continue
        dex = name.split(":", 1)[0].strip()
        if dex and dex not in seen:
            seen.add(dex)
            out.append(dex)
    return out


def collect_universe(info: Info, wanted_coins: set[str] | None = None) -> list[dict[str, Any]]:
    """Collect the default perp universe plus HIP-3 coins we explicitly want.

    HIP-3 names (xyz:TSLA) are not in the core meta() or all_mids() catalogs.
    Probe preferred builder dexes first, then a short prefix of perpDexs — do not
    walk every testnet dex.
    """
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_meta(meta: dict[str, Any]) -> None:
        for item in meta.get("universe") or []:
            name = item.get("name")
            if name and name not in seen:
                seen.add(name)
                assets.append(item)

    def shorts() -> set[str]:
        names = {str(item.get("name", "")).upper() for item in assets}
        names.update(name.split(":")[-1] for name in list(names))
        return names

    add_meta(info.meta())

    wanted = {coin.upper() for coin in (wanted_coins or set())}
    missing = wanted - shorts()
    if not missing:
        return assets

    try:
        mids = info.all_mids() or {}
    except Exception as exc:
        logger.debug("all_mids() unavailable: %s", exc)
        mids = {}

    extra_dexes: set[str] = set()
    for name in mids:
        short = name.split(":")[-1].upper()
        if short not in missing:
            continue
        if ":" in name:
            extra_dexes.add(name.split(":", 1)[0])
        if name not in seen:
            assets.append({"name": name, "szDecimals": 0})
            seen.add(name)
        missing.discard(short)

    for dex in extra_dexes:
        try:
            add_meta(info.meta(dex=dex))
        except Exception as exc:
            logger.debug("Could not load meta for dex %s: %s", dex, exc)

    missing = wanted - shorts()
    if not missing:
        return assets

    dex_names = _perp_dex_names(info)
    preferred = [name for name in ("xyz", "flx") if name in dex_names]
    rest = [name for name in dex_names if name not in preferred]
    probed = 0
    for dex in preferred + rest:
        if not missing:
            break
        if probed >= 20:
            logger.warning("Stopped HIP-3 scan after %s dexes; still missing %s", probed, sorted(missing))
            break
        try:
            add_meta(info.meta(dex=dex))
        except Exception as exc:
            logger.debug("Could not load meta for dex %s: %s", dex, exc)
            continue
        probed += 1
        missing = wanted - shorts()

    if missing:
        logger.warning("HIP-3 coins still missing after dex scan: %s", sorted(missing))
    return assets


def _perp_dex_names(info: Info) -> list[str]:
    try:
        raw = info.perp_dexs() or []
    except Exception as exc:
        logger.debug("perp_dexs() unavailable: %s", exc)
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


class HyperliquidWallet:
    def __init__(
        self,
        wallet_id: str,
        private_key: str,
        leverage: float,
        collateral_percentage: float,
        network: str,
        account_address: str = "",
        perp_dexs: list[str] | None = None,
    ):
        self.wallet_id = wallet_id
        self.leverage = leverage
        self.collateral_percentage = collateral_percentage
        self.ready = False
        key = private_key.strip()
        if not key.startswith("0x"):
            key = "0x" + key
        self.account = Account.from_key(key)
        self.address = account_address.strip() or self.account.address
        base_url = api_url(network)
        self.extra_dexes = [dex for dex in (perp_dexs or []) if dex]
        sdk_dexs = ["", *self.extra_dexes] if self.extra_dexes else None
        if self.extra_dexes:
            logger.info("Wallet %s loading HIP-3 dex(es): %s", wallet_id, ", ".join(self.extra_dexes))
        self.info = Info(base_url, skip_ws=True, perp_dexs=sdk_dexs)
        self.exchange = Exchange(
            self.account,
            base_url,
            account_address=self.address,
            perp_dexs=sdk_dexs,
        )
        self.ready = True
        logger.info("Wallet %s ready (%s) L:%sx C:%s%%", wallet_id, self.address[:10], leverage, collateral_percentage)

    def user_state(self) -> dict[str, Any]:
        state = self.info.user_state(self.address) or {}
        if not self.extra_dexes:
            return state
        positions = list(state.get("assetPositions") or [])
        seen = {(item.get("position") or {}).get("coin") for item in positions}
        for dex in self.extra_dexes:
            try:
                extra = self.info.user_state(self.address, dex=dex) or {}
            except Exception as exc:
                logger.debug("user_state(dex=%s) unavailable: %s", dex, exc)
                continue
            for item in extra.get("assetPositions") or []:
                coin = (item.get("position") or {}).get("coin")
                if coin and coin not in seen:
                    positions.append(item)
                    seen.add(coin)
        state["assetPositions"] = positions
        return state

    def _open_orders(self) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        seen: set[Any] = set()
        dexes = [""] + self.extra_dexes
        for dex in dexes:
            try:
                raw = self.info.frontend_open_orders(self.address, dex=dex) or []
            except Exception:
                try:
                    raw = self.info.open_orders(self.address, dex=dex) or []
                except Exception:
                    raw = []
            for order in raw:
                key = order.get("oid") or (order.get("coin"), order.get("limitPx"), order.get("sz"))
                if key in seen:
                    continue
                seen.add(key)
                orders.append(order)
        return orders

    def spot_user_state(self) -> dict[str, Any]:
        return self.info.spot_user_state(self.address) or {}

    def abstraction_mode(self) -> str:
        try:
            raw = self.info.query_user_abstraction_state(self.address)
        except Exception as exc:
            logger.debug("userAbstraction unavailable: %s", exc)
            return "default"
        if isinstance(raw, str) and raw:
            return raw
        return "default"

    def mid_price(self, coin: str) -> float:
        mids = self._mids_lookup(coin)
        if coin not in mids:
            raise ValueError(f"No mid price for {coin}")
        return float(mids[coin])

    def _mids_lookup(self, coin: str | None = None) -> dict[str, Any]:
        mids = dict(self.info.all_mids() or {})
        if coin and coin not in mids and ":" in coin:
            dex = coin.split(":", 1)[0]
            try:
                mids.update(self.info.all_mids(dex=dex) or {})
            except Exception as exc:
                logger.debug("all_mids(dex=%s) unavailable: %s", dex, exc)
        return mids

    def position_size(self, coin: str) -> float:
        state = self.user_state()
        for item in state.get("assetPositions") or []:
            pos = item.get("position") or {}
            if pos.get("coin") == coin:
                return float(pos.get("szi") or 0)
        return 0.0

    def account_value(self) -> float:
        return trading_account_value(
            self.user_state(),
            self.spot_user_state(),
            self.abstraction_mode(),
        )

    def wallet_snapshot(self) -> dict[str, Any]:
        state = self.user_state()
        try:
            mids = {str(k): float(v) for k, v in (self.info.all_mids() or {}).items()}
        except Exception:
            mids = {}

        positions = []
        for item in state.get("assetPositions") or []:
            pos = item.get("position") or {}
            szi = float(pos.get("szi") or 0)
            if szi == 0:
                continue
            coin = pos.get("coin")
            mark = mids.get(str(coin))
            if mark is None and coin:
                try:
                    extra = self._mids_lookup(str(coin))
                    raw = extra.get(str(coin)) or extra.get(str(coin).split(":")[-1])
                    mark = float(raw) if raw not in (None, "") else None
                except (TypeError, ValueError):
                    mark = mids.get(str(coin).split(":")[-1])
            entry = pos.get("entryPx")
            pnl = pos.get("unrealizedPnl")
            value = abs(szi) * mark if mark is not None else None
            positions.append(
                {
                    "coin": coin,
                    "size": szi,
                    "side": "long" if szi > 0 else "short",
                    "entry_px": float(entry) if entry not in (None, "") else None,
                    "mark_px": mark,
                    "value": value,
                    "unrealized_pnl": float(pnl) if pnl not in (None, "") else 0.0,
                }
            )

        orders = []
        raw_orders = self._open_orders()
        for order in raw_orders:
            trigger_raw = order.get("triggerPx")
            try:
                trigger_px = float(trigger_raw) if trigger_raw not in (None, "") else None
            except (TypeError, ValueError):
                trigger_px = None
            if trigger_px == 0:
                trigger_px = None
            limit_raw = order.get("limitPx")
            try:
                limit_px = float(limit_raw) if limit_raw not in (None, "") else None
            except (TypeError, ValueError):
                limit_px = None
            orders.append(
                {
                    "oid": order.get("oid"),
                    "coin": order.get("coin"),
                    "side": "long" if order.get("side") == "B" else "short",
                    "size": float(order.get("sz") or 0),
                    "limit_px": limit_px,
                    "trigger_px": trigger_px,
                    "is_trigger": bool(order.get("isTrigger")),
                    "reduce_only": bool(order.get("reduceOnly")),
                    "order_type": order.get("orderType") or ("trigger" if order.get("isTrigger") else "limit"),
                }
            )

        spot_state = {}
        try:
            spot_state = self.spot_user_state()
        except Exception as exc:
            logger.debug("spot_user_state failed: %s", exc)
        abstraction = self.abstraction_mode()
        spot_usdc = _spot_usdc_available(spot_state)
        perp_value = _perp_account_value(state)
        account_value = trading_account_value(state, spot_state, abstraction)
        assets = spot_assets_from_state(spot_state, mids)
        wallet_value = wallet_nav(perp_value, assets, abstraction)
        withdrawable = float(state.get("withdrawable") or 0)
        if abstraction in UNIFIED_ACCOUNT_MODES:
            withdrawable = max(withdrawable, spot_usdc)

        return {
            "wallet_id": self.wallet_id,
            "address": self.address,
            "abstraction": abstraction,
            "wallet_value": wallet_value,
            "account_value": account_value,
            "perp_account_value": perp_value,
            "spot_usdc": spot_usdc,
            "withdrawable": withdrawable,
            "assets": assets,
            "positions": positions,
            "open_orders": orders,
        }

    def calculate_size(self, coin: str, sz_decimals: int, qty_percentage: float | None) -> float:
        pct = qty_percentage if qty_percentage is not None else self.collateral_percentage
        mid = self.mid_price(coin)
        notional = self.account_value() * (pct / 100.0) * self.leverage
        if mid <= 0 or notional <= 0:
            raise ValueError(
                "Cannot size position: mid or account value is zero "
                "(unified accounts keep USDC in spot, not perps)"
            )
        size = _round_size(notional / mid, sz_decimals)
        if size <= 0:
            raise ValueError("Calculated size is zero — increase collateral or leverage")
        return size

    def set_leverage(self, coin: str) -> None:
        try:
            self.exchange.update_leverage(int(self.leverage), coin, is_cross=True)
        except Exception as exc:
            logger.warning("Could not update leverage for %s/%s: %s", self.wallet_id, coin, exc)

    def market_open(self, coin: str, is_buy: bool, size: float) -> Any:
        slip = market_slippage(coin)
        if slip != CORE_MARKET_SLIPPAGE:
            logger.info("HIP-3 market open %s slippage=%s", coin, slip)
        return self.exchange.market_open(coin, is_buy, size, None, slip)

    def market_close(self, coin: str, size: float | None = None) -> Any:
        return self.exchange.market_close(coin, sz=size, slippage=market_slippage(coin))

    def place_tpsl(
        self,
        coin: str,
        close_is_buy: bool,
        size: float,
        sz_decimals: int,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> Any:
        orders: list[dict[str, Any]] = []
        for price, tpsl in ((stop_loss, "sl"), (take_profit, "tp")):
            if price is None:
                continue
            trigger_px = _round_px(price, sz_decimals)
            orders.append(
                {
                    "coin": coin,
                    "is_buy": close_is_buy,
                    "sz": size,
                    "limit_px": trigger_limit_px(close_is_buy, trigger_px, sz_decimals),
                    "order_type": {
                        "trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": tpsl}
                    },
                    "reduce_only": True,
                }
            )
        if not orders:
            return None
        grouping = "positionTpsl" if len(orders) > 1 else "na"
        return self.exchange.bulk_orders(orders, grouping=grouping)

    async def wait_flat(self, coin: str, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if abs(self.position_size(coin)) < 1e-12:
                return True
            await asyncio.sleep(0.4)
        return abs(self.position_size(coin)) < 1e-12
