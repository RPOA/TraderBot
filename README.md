# TraderBot

Infra to capture TradingView alerts and trade on HyperLiquid.

Two Docker services:

- **watcher** — Selenium scrapes the TradingView alert log and forwards messages unchanged
- **trader** — receives those messages, applies direction / risk rules, and executes on HyperLiquid

```
TradingView  --Selenium-->  watcher  --POST /do_trade|/trend-->  trader  --SDK-->  HyperLiquid
```

## Quick start

```bash
cp .env.example .env
cp services/trader/wallets.json.example services/trader/wallets.json
# edit .env (WEBHOOK_SECRET) and wallets.json (EVM private key — prefer an agent wallet)

docker compose up --build
```

- Trader API: `http://localhost:18080` (container 8080)
- Watcher API: `http://localhost:18090` (container 8090)
- Watcher VNC (first-time TradingView login): `localhost:15900`

TradingView blocks automated login (reCAPTCHA). Connect to VNC, sign in once; the Chrome profile is persisted in `chrome-profile/`.

## Trader contract

`POST /do_trade` — only order endpoint

```
SECRET||##action/ticker[/sl/tp[/qty][/order_id]]##[@wallet_id]
```

Examples:

```
SECRET||##buy/SOLUSDT.P##
SECRET||##buy/SOLUSDT.P/0/0##
SECRET||##sell/TSLAUSDT.P/180/200/15##@main
SECRET||##close/SOLUSDT.P##
```

`POST /trend` — direction `short` | `long` | `all`

```
SECRET||**trend/all**
```

Rules:

- `short` accepts only sell; `long` only buy; `all` accepts both. `close` always passes.
- SL and TP both `0` (or omitted) means no trigger orders. A single `0` leaves the other active.
- Existing position on the same coin is closed and confirmed flat before a new order (HyperLiquid may reject pyramiding).
- `CLOSE_ON_TREND_CHANGE=true` closes conflicting positions on any `/trend` flip, including to/from `all`.

Tickers live in `services/trader/tickers.yaml` and are resolved against the live HyperLiquid universe on startup (SOL, BTC, ETH, TSLA by default).

Supervision: `GET /health` `/status` `/logs` `/wallet` `/direction` `/tickers`

## Watcher

Alert names are comma-separated in `.env`:

- `TRADE_ALERT_NAMES` → `/do_trade`
- `TREND_ALERT_NAMES` → `/trend`

Alerts must include `{{timenow}}` so they can be deduplicated.

Supervision: `GET /health` `/status` `/logs` `/orders` `/alerts`  
`POST /control/restart_browser` restarts the Selenium loop without killing the container.

## Local tests

```bash
pip install -r services/trader/requirements.txt
(cd services/trader && pytest)
pip install -r services/watcher/requirements.txt
(cd services/watcher && pytest)
```
