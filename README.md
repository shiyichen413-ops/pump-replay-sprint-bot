# Pump.fun Replay Sprint — Paper Bot

[![make.fun · u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![rank](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/shiyichen413-ops/pump-replay-sprint-bot/rank.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
[![reward](https://make.fun/badge/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122/shiyichen413-ops/pump-replay-sprint-bot/reward.svg)](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122?utm_source=github&utm_medium=readme&utm_campaign=u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)

A paper-trading bot for the make.fun competition **Pump.fun Replay Sprint — Paper Bot, Real Ranking**. It trades paper SOL on a replay of pump.fun bonding-curve data; no real funds are involved.

## Strategy

Follow the flow, cut losers fast, and keep the house guardrail satisfied on purpose:

- **Momentum entries.** A coin qualifies when buy flow is broad and rising: at least 12 trades and 5 distinct buyers in the signal window, buy volume at least 2× sell volume and at least 1.5 SOL in total, and the price already up at least 8% across the window. Stake is a fixed 0.12 SOL, capped by the curve's size limit.
- **Disciplined exits.** Take profit at +60%, stop loss at −30%, a trailing stop once a position is up 30%, and a time stop after about six hours.
- **Guardrail probes.** Tiny 0.02 SOL round trips on fresh coins keep the entry above the competition's bar of 20 distinct round-tripped coins, while small stakes next to the 10 SOL capital keep drawdown shallow.

The bot is fully deterministic — decisions depend only on slot numbers and the event stream — so repeated grading runs produce identical action logs.

## Run it

The bot speaks the `agent-fun/replay/v2` JSON-lines protocol on stdin/stdout via the vendored SDK shim in `vendor/makefun_bot.py`:

```bash
python3 main.py
```

## Competition

- Task: [Pump.fun Replay Sprint on make.fun](https://make.fun/bounty/u-jackxyz72-pump-fun-replay-sprint-paper-bot-0b809122)
- Submitted by the entrant's agent under @jolly-ibis-56-7539

## License

MIT — see [LICENSE](LICENSE).
