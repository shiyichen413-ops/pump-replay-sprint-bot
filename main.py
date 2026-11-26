"""Pump.fun Replay Sprint — paper trading bot (make.fun entry).

Strategy: follow the flow, cut losers fast, and keep the house
guardrail satisfied on purpose.

The bot watches the trade tape of every bonding curve it sees. A coin
becomes a momentum candidate when real money is flowing in faster
than it flows out: many distinct buyers, buy volume well above sell
volume over a short window, and a price that is already rising. It
buys a small fixed stake, then exits on a take-profit, a stop-loss, a
trailing stop after a strong run, or a time limit — whichever comes
first.

Separately, it runs tiny "probe" round trips on fresh coins so the
entry clears the competition guardrail (round trips of at least
0.01 SOL on at least 20 distinct coins, max drawdown at most 50%).
Position sizes are small next to the 10 SOL paper capital, which
keeps drawdown shallow by construction.

Everything is deterministic: decisions depend only on slot numbers
and the event stream, never on the clock or on randomness, so the
two grading runs produce identical action logs.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402

SOL = makefun_bot.LAMPORTS_PER_SOL

# --- tape windows (slots; roughly 16,800 slots per hour) ---
TAPE_WINDOW = 6000          # trades remembered per coin
SIGNAL_WINDOW = 4500        # window the momentum signal reads

# --- momentum entries ---
MIN_AGE_SLOTS = 3000        # ignore brand-new curves
MIN_TRADES = 12
MIN_BUYERS = 5
MIN_BUY_SOL = 3 * SOL // 2  # 1.5 SOL of buy flow in the window
BUY_SELL_RATIO = 2.0
MIN_PRICE_RISE = 1.08       # price up at least 8% across the window
MIN_REAL_QUOTE = 5 * SOL    # curve must hold some real depth
STAKE = 12 * SOL // 100     # 0.12 SOL per momentum position
CASH_RESERVE = SOL          # never let free cash fall below 1 SOL
REENTRY_COOLDOWN = 20000    # slots before a coin may be bought again

# --- exits ---
TAKE_PROFIT = 1.60
STOP_LOSS = 0.70
TRAIL_ARM = 1.30            # trailing stop arms at +30%
TRAIL_DROP = 0.75           # and fires at 75% of the peak value
MAX_HOLD_SLOTS = 100000     # about six hours

# --- guardrail probes ---
PROBE_STAKE = 2 * SOL // 100    # 0.02 SOL, above the 0.01 SOL bar
PROBE_TARGET = 20               # distinct coins to round-trip
PROBE_EVERY = 2000              # slots between probe buys
PROBE_MAX_OPEN = 2
PROBE_HOLD = 3000               # slots, then sell whatever happened
PROBE_MIN_BUY_TRADES = 3

MAX_SLIPPAGE_BPS = 3000

# Per-coin tape: list of (slot, is_buy, sol_amount, user, price).
tape = {}
# Per-coin bookkeeping that is only ever read by key, never iterated
# in a way that could depend on hash order.
first_price = {}      # mint -> price at the start of the tape window
peak_ratio = {}       # mint -> best value ratio seen while held
entry_slot = {}       # mint -> slot the current position was opened
bought_cost = {}      # mint -> lamports spent on the open/last position
closed_coins = {}     # mint -> slot its last position closed
probe_mints = set()   # mints currently held as probes (membership only)
round_trips = 0       # distinct coins fully round-tripped so far
last_probe_slot = -10 ** 9


def price_of(curve):
    """Spot price in lamports per token unit from virtual reserves."""
    if curve is None or curve.virtual_token_reserves <= 0:
        return 0.0
    return curve.virtual_quote_reserves / curve.virtual_token_reserves


def update_tape(state, batch):
    """Fold this batch's trade events into the per-coin tape."""
    slot = batch["slot"]
    cutoff = slot - TAPE_WINDOW
    for e in batch["events"]:
        if e["event"] != "TradeEvent":
            continue
        mint = e["mint"]
        curve = state.curves.get(mint)
        price = price_of(curve)
        rows = tape.get(mint)
        if rows is None:
            rows = tape[mint] = []
            first_price[mint] = price
        rows.append((e["slot"], bool(e["is_buy"]), int(e["sol_amount"]),
                     e["user"], price))
        while rows and rows[0][0] < cutoff:
            rows.pop(0)
        if rows:
            first_price[mint] = rows[0][4]


def window_stats(mint, slot):
    """Aggregate the signal window for one coin.

    Returns (trades, buy_sol, sell_sol, distinct_buyers) or None when
    the coin has no tape in the window.
    """
    rows = tape.get(mint)
    if not rows:
        return None
    cutoff = slot - SIGNAL_WINDOW
    trades = 0
    buy_sol = 0
    sell_sol = 0
    buyers = set()
    for s, is_buy, amount, user, _price in rows:
        if s < cutoff:
            continue
        trades += 1
        if is_buy:
            buy_sol += amount
            buyers.add(user)
        else:
            sell_sol += amount
    if trades == 0:
        return None
    return trades, buy_sol, sell_sol, len(buyers)


def momentum_ok(state, mint, slot):
    """True when a coin shows real, broad, rising buy flow."""
    curve = state.curves.get(mint)
    if curve is None or curve.complete or curve.created_slot is None:
        return False
    if slot - curve.created_slot < MIN_AGE_SLOTS:
        return False
    if curve.real_quote_reserves < MIN_REAL_QUOTE:
        return False
    stats = window_stats(mint, slot)
    if stats is None:
        return False
    trades, buy_sol, sell_sol, buyers = stats
    if trades < MIN_TRADES or buyers < MIN_BUYERS or buy_sol < MIN_BUY_SOL:
        return False
    if buy_sol < BUY_SELL_RATIO * max(sell_sol, 1):
        return False
    start = first_price.get(mint, 0.0)
    now = price_of(curve)
    if start <= 0.0 or now < start * MIN_PRICE_RISE:
        return False
    return True


def note_closed_positions(state):
    """Count a round trip when a bought coin leaves the book."""
    global round_trips
    for mint in list(bought_cost):
        if mint in state.positions:
            continue
        cost = bought_cost.pop(mint)
        probe_mints.discard(mint)
        peak_ratio.pop(mint, None)
        entry_slot.pop(mint, None)
        closed_coins[mint] = state.slot
        if cost >= SOL // 100:
            round_trips += 1


def pending_mints(state, side):
    """Mints with an order of the given side still in flight."""
    out = set()
    for p in state.pending.values():
        if p.order["side"] == side:
            out.add(p.order["mint"])
    return out


def exit_orders(state, slot):
    """Sell orders for held positions that hit an exit rule."""
    orders = []
    selling = pending_mints(state, "sell")
    for mint, holding in state.positions.items():
        if mint in selling or holding.token_amount <= 0:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        now = price_of(curve)
        if now <= 0.0 or holding.cost_lamports <= 0:
            continue
        ratio = (now * holding.token_amount) / holding.cost_lamports
        peak = peak_ratio.get(mint, ratio)
        if ratio > peak:
            peak = peak_ratio[mint] = ratio
        held_for = slot - entry_slot.get(mint, slot)
        is_probe = mint in probe_mints
        fire = (
            ratio >= TAKE_PROFIT
            or ratio <= STOP_LOSS
            or (peak >= TRAIL_ARM and ratio <= peak * TRAIL_DROP)
            or held_for >= MAX_HOLD_SLOTS
            or (is_probe and held_for >= PROBE_HOLD)
            or (is_probe and (ratio >= 1.10 or ratio <= 0.90))
        )
        if fire:
            orders.append({
                "side": "sell",
                "mint": mint,
                "token_amount": holding.token_amount,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            })
            if is_probe:
                probe_mints.discard(mint)
    return orders


def entry_orders(state, batch, budget_left):
    """Momentum buys first, then guardrail probes while they are due."""
    global last_probe_slot
    orders = []
    slot = batch["slot"]
    caps = state.hello["caps"]
    tx = state.tx_fee_lamports()
    free = state.free_cash_lamports()
    open_slots = caps["max_open_positions"] - state.committed_position_count()
    buying = pending_mints(state, "buy")

    def afford(amount):
        return (len(orders) < budget_left and open_slots > 0
                and free - amount - tx >= CASH_RESERVE)

    def place(mint, amount):
        nonlocal free, open_slots
        orders.append({
            "side": "buy",
            "mint": mint,
            "quote_lamports": amount,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        })
        bought_cost[mint] = bought_cost.get(mint, 0) + amount
        entry_slot[mint] = slot
        free -= amount + tx
        open_slots -= 1

    # Coins that traded in this batch, in stream order, deduplicated.
    active = []
    seen = set()
    for e in batch["events"]:
        mint = e["mint"]
        if mint not in seen:
            seen.add(mint)
            active.append(mint)

    for mint in active:
        if mint in state.positions or mint in buying:
            continue
        closed = closed_coins.get(mint)
        if closed is not None and slot - closed < REENTRY_COOLDOWN:
            continue
        if not momentum_ok(state, mint, slot):
            continue
        amount = min(STAKE, state.max_buy_lamports(mint))
        if amount < SOL // 100 or not afford(amount):
            continue
        place(mint, amount)

    probes_open = sum(1 for m in probe_mints if m in state.positions)
    if (round_trips + probes_open < PROBE_TARGET
            and slot - last_probe_slot >= PROBE_EVERY
            and state.committed_position_count() < caps["max_open_positions"] - 1):
        for mint in active:
            if mint in state.positions or mint in buying:
                continue
            curve = state.curves.get(mint)
            if curve is None or curve.complete or curve.created_slot is None:
                continue
            if slot - curve.created_slot > 20000:
                continue
            rows = tape.get(mint) or []
            buys = sum(1 for r in rows if r[1])
            if buys < PROBE_MIN_BUY_TRADES:
                continue
            amount = min(PROBE_STAKE, state.max_buy_lamports(mint))
            if amount < SOL // 100 or not afford(amount):
                continue
            place(mint, amount)
            probe_mints.add(mint)
            last_probe_slot = slot
            break
    return orders


def on_batch(state, batch):
    update_tape(state, batch)
    note_closed_positions(state)
    max_orders = state.hello["caps"]["max_orders_per_batch"]
    orders = exit_orders(state, batch["slot"])
    room = max_orders - len(orders)
    if room > 0:
        orders.extend(entry_orders(state, batch, room))
    return orders[:max_orders]


if __name__ == "__main__":
    makefun_bot.run(on_batch)

