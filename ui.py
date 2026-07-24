import json
import os
import threading
import time
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify
from config import SYMBOL, TIMEFRAME, SL_PIPS, RISK_PER_TRADE, BE_PIPS, BE_ENABLED, FIXED_LOT
from broker import initialize, login, shutdown as broker_shutdown, is_connected, get_account_details
from market import get_current_price, get_previous_candle
from execution import get_open_positions, close_position
from risk import calculate_lot_size, calculate_stop_loss, calculate_take_profit
from logger import setup_logger

logger = setup_logger("ui")

app = Flask(__name__)

bot_running = False
bot_thread = None
last_log = []

LOG_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Execution Bot</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }
h1 { color: #58a6ff; margin-bottom: 20px; }
h2 { color: #8b949e; margin: 15px 0 10px; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-bottom: 20px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; }
.card label { font-size: 11px; color: #8b949e; text-transform: uppercase; }
.card .value { font-size: 22px; font-weight: bold; margin-top: 5px; }
.card .value.green { color: #3fb950; }
.card .value.red { color: #f85149; }
.card .value.blue { color: #58a6ff; }
.btn { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: bold; margin: 5px; }
.btn-buy { background: #238636; color: #fff; }
.btn-sell { background: #da3633; color: #fff; }
.btn-stop { background: #6e7681; color: #fff; }
.btn:hover { opacity: 0.85; }
.form-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: end; margin-bottom: 10px; }
.form-group { display: flex; flex-direction: column; }
.form-group label { font-size: 11px; color: #8b949e; margin-bottom: 4px; }
.form-group input, .form-group select { background: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 8px; border-radius: 4px; width: 120px; }
.log-box { background: #010409; border: 1px solid #30363d; border-radius: 8px; padding: 12px; height: 300px; overflow-y: auto; font-family: 'Consolas', monospace; font-size: 12px; line-height: 1.6; }
.log-entry { padding: 2px 0; border-bottom: 1px solid #21262d; }
.log-info { color: #58a6ff; }
.log-warn { color: #d29922; }
.log-error { color: #f85149; }
.log-success { color: #3fb950; }
.status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; }
.status-connected { background: #3fb950; }
.status-disconnected { background: #f85149; }
</style>
</head>
<body>
<h1>&#x1F7E2; Execution Bot</h1>

<h2>Connection</h2>
<div class="grid">
  <div class="card">
    <label>Status</label>
    <div class="value {{ 'green' if connected else 'red' }}">
      <span class="status-dot {{ 'status-connected' if connected else 'status-disconnected' }}"></span>
      {{ 'Connected' if connected else 'Disconnected' }}
    </div>
  </div>
  <div class="card">
    <label>Login</label>
    <div class="value blue">{{ account_login or '---' }}</div>
  </div>
  <div class="card">
    <label>Balance</label>
    <div class="value green">${{ '{:,.2f}'.format(account_balance) if account_balance else '---' }}</div>
  </div>
  <div class="card">
    <label>Equity</label>
    <div class="value green">${{ '{:,.2f}'.format(account_equity) if account_equity else '---' }}</div>
  </div>
</div>

<h2>Price</h2>
<div class="grid">
  <div class="card">
    <label>Symbol</label>
    <div class="value blue">{{ symbol }}</div>
  </div>
  <div class="card">
    <label>Bid</label>
    <div class="value">${{ '{:.5f}'.format(bid) if bid else '---' }}</div>
  </div>
  <div class="card">
    <label>Ask</label>
    <div class="value">${{ '{:.5f}'.format(ask) if ask else '---' }}</div>
  </div>
  <div class="card">
    <label>Spread</label>
    <div class="value">${{ '{:.1f}'.format((ask - bid) * 10000) if (bid and ask) else '---' }} pips</div>
  </div>
</div>

<h2>Trade Control</h2>
<div class="card">
  <div class="form-row">
    <div class="form-group">
      <label>Direction</label>
      <select name="direction" id="direction">
        <option value="BUY">BUY</option>
        <option value="SELL">SELL</option>
      </select>
    </div>
    <div class="form-group">
      <label>Lot Size</label>
      <input type="number" id="lot" value="0.01" step="0.01" min="0.01">
    </div>
    <div class="form-group">
      <label>SL (pips)</label>
      <input type="number" id="sl_pips" value="{{ sl_pips }}" step="5">
    </div>
    <div class="form-group">
      <label>TP (pips)</label>
      <input type="number" id="tp_pips" value="{{ sl_pips * 5 }}" step="5">
    </div>
    <div class="form-group">
      <label>&nbsp;</label>
      <button class="btn btn-buy" onclick="executeTrade('BUY')">BUY</button>
      <button class="btn btn-sell" onclick="executeTrade('SELL')">SELL</button>
    </div>
  </div>
</div>

<h2>Open Positions</h2>
<div class="card" id="positions">
  <div style="color: #8b949e;">No positions loaded</div>
</div>

<h2>Bot Log</h2>
<div class="log-box" id="log-box">
  {% for entry in log_entries %}
  <div class="log-entry {{ entry.level }}">{{ entry.time }} {{ entry.message }}</div>
  {% endfor %}
</div>

<script>
function refreshData() {
  fetch('/api/status')
    .then(r => r.json())
    .then(data => {
      document.getElementById('status').innerHTML =
        '<span class="status-dot ' + (data.connected ? 'status-connected' : 'status-disconnected') + '"></span>' +
        (data.connected ? 'Connected' : 'Disconnected');
      document.getElementById('balance').textContent = data.balance ? '$' + data.balance.toFixed(2) : '---';
      document.getElementById('equity').textContent = data.equity ? '$' + data.equity.toFixed(2) : '---';
      document.getElementById('bid').textContent = data.bid ? data.bid.toFixed(5) : '---';
      document.getElementById('ask').textContent = data.ask ? data.ask.toFixed(5) : '---';
      if (data.bid && data.ask) {
        document.getElementById('spread').textContent = ((data.ask - data.bid) * 10000).toFixed(1) + ' pips';
      }
    });

  fetch('/api/positions')
    .then(r => r.json())
    .then(data => {
      const el = document.getElementById('positions');
      if (!data.length) { el.innerHTML = '<div style="color:#8b949e;">No open positions</div>'; return; }
      el.innerHTML = data.map(p => '<div style="padding:8px;border-bottom:1px solid #21262d;">' +
        '<strong>' + (p.type == 0 ? 'BUY' : 'SELL') + '</strong> ' +
        p.symbol + ' | Vol: ' + p.volume +
        ' | Entry: ' + p.price_open.toFixed(5) +
        ' | SL: ' + p.sl.toFixed(5) +
        ' | TP: ' + p.tp.toFixed(5) +
        ' | PnL: ' + p.profit.toFixed(2) +
        '</div>').join('');
    });
}

function executeTrade(dir) {
  const lot = document.getElementById('lot').value;
  const sl_pips = parseInt(document.getElementById('sl_pips').value);
  const tp_pips = parseInt(document.getElementById('tp_pips').value);
  fetch('/api/trade', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ direction: dir, lot: parseFloat(lot), sl_pips: sl_pips, tp_pips: tp_pips })
  })
  .then(r => r.json())
  .then(data => { alert(data.message); })
  .catch(e => alert('Error: ' + e));
}

setInterval(refreshData, 3000);
refreshData();
</script>
</body>
</html>"""

def get_template():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_template.html"), "r") as f:
        return f.read()

TEMPLATE = LOG_HTML


def add_log(level, message):
    ts = datetime.now().strftime("%H:%M:%S")
    last_log.append({"time": ts, "level": f"log-{level}", "message": message})
    if len(last_log) > 100:
        last_log.pop(0)


@app.route("/")
def dashboard():
    # Auto-initialize MT5 if not connected
    if not is_connected():
        if not initialize():
            logger.warning("MT5 initialization failed from UI")
        else:
            from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
            login(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH)

    connected = is_connected()
    account = get_account_details() if connected else None
    bid, ask = get_current_price(SYMBOL) if connected else (None, None)
    positions = get_open_positions(SYMBOL) if connected else []
    return render_template_string(
        TEMPLATE,
        connected=connected,
        account_login=account["login"] if account else None,
        account_balance=account["balance"] if account else None,
        account_equity=account["equity"] if account else None,
        symbol=SYMBOL,
        bid=bid,
        ask=ask,
        sl_pips=SL_PIPS,
        log_entries=reversed(last_log[-50:]),
    )


@app.route("/api/status")
def api_status():
    connected = is_connected()
    account = get_account_details() if connected else None
    bid, ask = get_current_price(SYMBOL) if connected else (None, None)
    return jsonify({
        "connected": connected,
        "balance": account["balance"] if account else None,
        "equity": account["equity"] if account else None,
        "bid": bid,
        "ask": ask,
    })


@app.route("/api/positions")
def api_positions():
    if not is_connected():
        return jsonify([])
    positions = get_open_positions(SYMBOL)
    return jsonify([
        {
            "ticket": p.ticket,
            "type": p.type,
            "symbol": p.symbol,
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
        }
        for p in positions
    ])


@app.route("/api/trade", methods=["POST"])
def api_trade():
    global bot_running
    data = request.get_json()
    direction = data.get("direction", "BUY")
    lot = data.get("lot", 0.01)
    sl_pips = data.get("sl_pips", SL_PIPS)
    tp_pips = data.get("tp_pips", SL_PIPS * 5)

    if not is_connected():
        return jsonify({"message": "Not connected to MT5"}), 400

    if has_open_position(SYMBOL):
        return jsonify({"message": "Position already open"}), 400

    bid, ask = get_current_price(SYMBOL)
    if bid is None or ask is None:
        return jsonify({"message": "Could not get price"}), 400

    entry = ask if direction == "BUY" else bid
    sl = round(entry - sl_pips * 0.0001 if direction == "BUY" else entry + sl_pips * 0.0001, 5)
    tp = round(entry + tp_pips * 0.0001 if direction == "BUY" else entry - tp_pips * 0.0001, 5)

    from execution import execute_buy, execute_sell
    if direction == "BUY":
        result = execute_buy(SYMBOL, lot, sl, tp, comment="UI Trade")
    else:
        result = execute_sell(SYMBOL, lot, sl, tp, comment="UI Trade")

    if result and result.retcode == 0:
        add_log("success", f"{direction} {lot} {SYMBOL} @ {entry} SL={sl} TP={tp}")
        return jsonify({"message": f"{direction} trade executed successfully"})
    else:
        add_log("error", f"{direction} trade failed")
        return jsonify({"message": "Trade failed"}), 500


def run_ui(host="0.0.0.0", port=5000):
    add_log("info", "UI server starting...")
    app.run(host=host, port=port, debug=False, use_reloader=False)