from enum import Enum


class Direction(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class Strategy:
    SYMBOL = "EURUSD"
    TIMEFRAME = "M15"

    DIRECTION = Direction.NONE

    SL_PIPS = 50
    TP_PIPS = 100
    RR_RATIO = 5

    RISK_MODE = "fixed_amount"
    RISK_AMOUNT = 100.0
    RISK_PERCENT = 1.0

    LOT_SIZE = 0.0

    BE_ENABLED = True
    BE_PIPS = 5

    MAX_OPEN_POSITIONS = 1
    MAX_DAILY_LOSS_PCT = 5.0

    MAGIC_NUMBER = 123456
    COMMENT = "Manual Signal"

    @classmethod
    def get_sl_tp(cls, entry_price, direction):
        point = cls._get_point()
        digits = cls._get_digits()

        sl_pips = cls.SL_PIPS
        tp_pips = cls.TP_PIPS

        if direction == Direction.BUY:
            sl = round(entry_price - sl_pips * point, digits)
            tp = round(entry_price + tp_pips * point, digits)
        else:
            sl = round(entry_price + sl_pips * point, digits)
            tp = round(entry_price - tp_pips * point, digits)

        return sl, tp

    @classmethod
    def get_tp_from_rr(cls, entry_price, sl_price, direction):
        point = cls._get_point()
        digits = cls._get_digits()

        distance = abs(entry_price - sl_price)
        tp_distance = distance * cls.RR_RATIO

        if direction == Direction.BUY:
            tp = round(entry_price + tp_distance, digits)
        else:
            tp = round(entry_price - tp_distance, digits)

        return tp

    @classmethod
    def to_dict(cls):
        return {
            "symbol": cls.SYMBOL,
            "timeframe": cls.TIMEFRAME,
            "direction": cls.DIRECTION.value,
            "sl_pips": cls.SL_PIPS,
            "tp_pips": cls.TP_PIPS,
            "rr_ratio": cls.RR_RATIO,
            "risk_mode": cls.RISK_MODE,
            "risk_amount": cls.RISK_AMOUNT,
            "risk_percent": cls.RISK_PERCENT,
            "lot_size": cls.LOT_SIZE,
            "be_enabled": cls.BE_ENABLED,
            "be_pips": cls.BE_PIPS,
            "max_positions": cls.MAX_OPEN_POSITIONS,
            "magic": cls.MAGIC_NUMBER,
            "comment": cls.COMMENT,
        }

    @staticmethod
    def _get_point():
        from symbol_store import symbol_info
        info = symbol_info(Strategy.SYMBOL)
        return info.point if info else 0.00001

    @staticmethod
    def _get_digits():
        from symbol_store import symbol_info
        info = symbol_info(Strategy.SYMBOL)
        return info.digits if info else 5


def print_strategy():
    s = Strategy.to_dict()
    print("=" * 50)
    print("  EXECUTION BOT - STRATEGY CONFIG")
    print("=" * 50)
    print(f"  Symbol:       {s['symbol']}")
    print(f"  Timeframe:    {s['timeframe']}")
    print(f"  Direction:    {s['direction']}")
    print(f"  SL:           {s['sl_pips']} pips")
    print(f"  TP:           {s['tp_pips']} pips ({s['rr_ratio']}:1 RR)")
    print(f"  Risk Mode:    {s['risk_mode']}")
    print(f"  Risk Amount:  ${s['risk_amount']}")
    print(f"  Lot Size:     {s['lot_size']}")
    print(f"  Break-Even:   {'ON' if s['be_enabled'] else 'OFF'} ({s['be_pips']} pips)")
    print(f"  Max Positions: {s['max_positions']}")
    print("=" * 50)