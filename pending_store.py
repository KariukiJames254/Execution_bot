import json
import os
import threading
from datetime import datetime

PENDING_TRADES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_trades.json")
_lock = threading.Lock()


def _load_pending_trades():
    """Load pending trades from disk."""
    try:
        if os.path.exists(PENDING_TRADES_FILE):
            with open(PENDING_TRADES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_pending_trades(trades):
    """Save pending trades to disk."""
    try:
        with open(PENDING_TRADES_FILE, "w", encoding="utf-8") as f:
            json.dump(trades, f, default=str, ensure_ascii=False)
    except Exception:
        pass


def save_pending_trades(trades):
    """Public wrapper to save pending trades to disk."""
    _save_pending_trades(trades)


def get_all_pending_trades():
    """Return all pending trades (in-memory + disk)."""
    with _lock:
        return _load_pending_trades()


def get_pending_trade(trade_id):
    """Get a single pending trade by ID."""
    with _lock:
        trades = _load_pending_trades()
        return trades.get(trade_id)


def set_pending_trade(trade_id, trade_data):
    """Store a pending trade."""
    with _lock:
        trades = _load_pending_trades()
        trades[trade_id] = trade_data
        _save_pending_trades(trades)


def update_pending_trade(trade_id, updates):
    """Update fields of an existing pending trade."""
    with _lock:
        trades = _load_pending_trades()
        if trade_id in trades:
            trades[trade_id].update(updates)
            _save_pending_trades(trades)
            return trades[trade_id]
        return None


def delete_pending_trade(trade_id, reason=""):
    """Remove a pending trade."""
    with _lock:
        trades = _load_pending_trades()
        if trade_id in trades:
            del trades[trade_id]
            _save_pending_trades(trades)
            return True
        return False


def get_pending_trades_by_status(status):
    """Get all pending trades with a specific status."""
    with _lock:
        trades = _load_pending_trades()
        return {tid: t for tid, t in trades.items() if t.get("status") == status}


def get_pending_trades_count():
    """Get count of pending trades."""
    with _lock:
        return len(_load_pending_trades())


def get_armed_trades_count():
    """Get count of armed pending trades."""
    with _lock:
        trades = _load_pending_trades()
        return sum(1 for t in trades.values() if t.get("status") == "armed")
