//+------------------------------------------------------------------+
//|                     ExecutionBridge.mq5 v5.0                      |
//|  - Pre-loads all trade params at arm time                         |
//|  - Executes locally with OrderSend() at candle close              |
//  - No Sleep() anywhere — fully event-driven via timer             |
//  - Broker-reported filling mode, configurable MagicNumber/Devi    |
//|  - Break-even via server-sent be_rr/be_trigger                    |
//|  - Rate-limited reporting: market tick, 30s symbol, 60s account |
//|  - Candle report only on new bar                                |
//|  - State machine: IDLE→ARMED→WAIT_CLOSE→MONITORING→FINISHED     |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "5.00"
#property strict

#ifndef TRADE_RETCODE_MARKET_CLOSED
#define TRADE_RETCODE_MARKET_CLOSED 10018
#endif

input string FlaskURL     = "http://102.203.116.146:5000";  // VPS Flask API endpoint
input bool   TEST_MODE    = false;
input long   MagicNumber  = 123456;   // Configurable magic number
input int    Deviation    = 10;       // Slippage in points

enum TradeState {
   STATE_IDLE,         // Polling Flask for armed trades
   STATE_ARMED,        // Trade armed, waiting for candle close
   STATE_EXECUTED,     // OrderSend DONE, monitoring position
   STATE_CANCELLED,    // Trade was cancelled by Flask
   STATE_ERROR,        // Execution or connection error
   STATE_DONE           // Post-execution cleanup
};
TradeState currentState = STATE_IDLE;

string pendingTradeId = "";
string pendingDirection = "";
string pendingSymbol = "";
datetime candleCloseTime = 0;
string armedSymbol = "";
int armedTfMinutes = 15;
string ea_requested_symbol = "";
string trackedTradeId = "";
datetime armedTime = 0;
datetime armedBarTime = 0;

double pendingLot = 0;
double pendingSl = 0;
double pendingTp = 0;
double pendingEntry = 0;
double pendingRiskDistance = 0;
double pendingBeRr = 0;
double pendingBeTrigger = 0;
double pendingManualSl = 0;
double pendingRiskAmount = 310.0;
double pendingRrRatio = 5.0;
bool breakEvenApplied = false;
datetime lastBeAttemptTime = 0;

string lastMarketReport = "";

void Log(string msg)
{
    Print("[ExecutionBridge] ", msg);
}

ENUM_TIMEFRAMES _PeriodToTf(int tf_minutes)
{
   if(tf_minutes <= 1) return PERIOD_M1;
   else if(tf_minutes <= 5) return PERIOD_M5;
   else if(tf_minutes <= 15) return PERIOD_M15;
   else if(tf_minutes <= 30) return PERIOD_M30;
   else if(tf_minutes <= 60) return PERIOD_H1;
   else if(tf_minutes <= 240) return PERIOD_H4;
   else return PERIOD_D1;
}

string TimeframeToString(ENUM_TIMEFRAMES tf)
{
   if(tf == PERIOD_M1) return "M1";
   if(tf == PERIOD_M5) return "M5";
   if(tf == PERIOD_M15) return "M15";
   if(tf == PERIOD_M30) return "M30";
   if(tf == PERIOD_H1) return "H1";
   if(tf == PERIOD_H4) return "H4";
   if(tf == PERIOD_D1) return "D1";
   return "UNKNOWN";
}

bool EnsureSymbol(string symbol)
{
    if(symbol == "") return false;
    long sel = SymbolInfoInteger(symbol, SYMBOL_SELECT);
    if(sel != 0) return true;
    if(SymbolSelect(symbol, true)) return true;
    return false;
}

bool SendPostRequest(string url, string jsonPayload, string &response);
bool SendGetRequest(string url, string &response);
string ExtractJsonValue(string json, string key);
string Trim(string value);
void ExecuteTradeLocal();
void ClosePositionByTicket(long ticket, string symbol);
void ReportMarket();
void ReportTick(string symbol, double bid, double ask);
void ReportTickFor(string symbol);
void ReportPosition();
void ReportSymbolInfo();
void ReportSymbolInfoFor(string symbol);
void ReportCandleFor(string symbol, ENUM_TIMEFRAMES tf);
void ReportAccount();
void ReportExecutionDetailed(string tradeId, string status, int retcode, string comment,
                             long ticket, long deal, double entry, double slippage, double spread);
void _cleanupTrade();

void _cleanupTrade()
{
    pendingTradeId     = "";
    pendingDirection   = "";
    pendingSymbol      = "";
    pendingLot         = 0;
    pendingSl          = 0;
    pendingTp          = 0;
    pendingEntry       = 0;
    pendingRiskDistance = 0;
    pendingBeRr        = 0;
    pendingBeTrigger   = 0;
    pendingManualSl    = 0;
    pendingRiskAmount  = 310.0;
    pendingRrRatio     = 5.0;
    breakEvenApplied   = false;
    trackedTradeId     = "";
    candleCloseTime    = 0;
    armedTime          = 0;
    armedBarTime       = 0;
    armedSymbol        = "";
    armedTfMinutes     = 15;
    Comment("");
}

void LogTimeDiagnostics(string symbol, ENUM_TIMEFRAMES tf)
{
    if(symbol == "" || tf == 0) return;

    datetime now = TimeCurrent();
    datetime timeLocal = TimeLocal();
    datetime timeServer = TimeTradeServer();
    datetime timeGMT = TimeGMT();

    MqlTick tick;
    datetime tickTime = 0;
    double tickBid = 0;
    double tickAsk = 0;
    if(SymbolInfoTick(symbol, tick))
    {
        tickTime = (datetime)tick.time;
        tickBid = tick.bid;
        tickAsk = tick.ask;
    }

    datetime bar0 = iTime(symbol, tf, 0);
    datetime bar1 = iTime(symbol, tf, 1);
    long synced = 0;
    long barsCount = 0;
    if(bar0 > 0)
    {
        synced = SeriesInfoInteger(symbol, tf, SERIES_SYNCHRONIZED);
        barsCount = Bars(symbol, tf);
    }

    long periodSeconds = PeriodSeconds(tf);
    datetime barClose0 = (bar0 > 0) ? (bar0 + periodSeconds) : 0;
    long secondsToClose = (barClose0 > 0) ? (long)(barClose0 - now) : 0;

    Log("[TimeDiagnostic] Symbol=" + symbol + " TF=" + TimeframeToString(tf) +
        " TimeCurrent=" + TimeToString(now) +
        " TimeLocal=" + TimeToString(timeLocal) +
        " TimeTradeServer=" + TimeToString(timeServer) +
        " TimeGMT=" + TimeToString(timeGMT) +
        " TickTime=" + (tickTime > 0 ? TimeToString(tickTime) : "N/A") +
        " TickBid=" + DoubleToString(tickBid, 5) +
        " TickAsk=" + DoubleToString(tickAsk, 5) +
        " Bar0=" + (bar0 > 0 ? TimeToString(bar0) : "N/A") +
        " Bar1=" + (bar1 > 0 ? TimeToString(bar1) : "N/A") +
        " BarClose0=" + (barClose0 > 0 ? TimeToString(barClose0) : "N/A") +
        " SecondsToClose=" + (string)secondsToClose +
        " SeriesSynced=" + (string)synced +
        " BarsCount=" + (string)barsCount);
}

int OnInit()
{
   Log("Started v5 - Local Execute, No Sleep, Broker Fill, Rate-Limited");
   EventSetTimer(1);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Log("Stopped");
}

void OnTimer()
{
    ReportMarket();

    // Symbol info: every 2 seconds when EA is connected/idle or armed (needed for preflight)
    // Reduced to 30 seconds when monitoring an active position
    static datetime lastSymbolInfoReport = 0;
    int symbolInfoInterval = (currentState == STATE_EXECUTED) ? 30 : 2;
    if(TimeCurrent() - lastSymbolInfoReport >= symbolInfoInterval)
    {
        ReportSymbolInfo();

        // Always report current chart symbol's candle so pre-flight can read it
        ReportCandleFor(_Symbol, _PeriodToTf(armedTfMinutes));

        if(armedSymbol != "" && armedSymbol != _Symbol)
        {
            ReportSymbolInfoFor(armedSymbol);
            ReportCandleFor(armedSymbol, _PeriodToTf(armedTfMinutes));
            ReportTickFor(armedSymbol);
        }

        string reqSym = ea_requested_symbol;
        if(reqSym != "" && reqSym != _Symbol && reqSym != armedSymbol)
        {
            EnsureSymbol(reqSym);
            ReportSymbolInfoFor(reqSym);
            ReportCandleFor(reqSym, _PeriodToTf(armedTfMinutes));
            ReportTickFor(reqSym);
        }

        // Report symbol info for armed symbol if different from chart
        if(armedSymbol != "" && armedSymbol != _Symbol)
        {
            ReportTickFor(armedSymbol);
        }
        lastSymbolInfoReport = TimeCurrent();
    }

    // Account info: every 60 seconds
    static datetime lastAccountReport = 0;
    if(TimeCurrent() - lastAccountReport >= 60)
    {
        ReportAccount();
        lastAccountReport = TimeCurrent();
    }

    // Post-execution / error / cancelled states
    if(currentState != STATE_IDLE && currentState != STATE_ARMED)
    {
        ReportPosition();

        // Break-even check (only after successful execution)
        if(currentState == STATE_EXECUTED && pendingSymbol != ""
           && pendingBeRr > 0 && pendingBeTrigger > 0 && !breakEvenApplied)
        {
            // Backoff: only retry every 3 seconds to avoid hammering trade context
            if(TimeCurrent() - lastBeAttemptTime < 3 && lastBeAttemptTime > 0)
            {
                // Too soon since last attempt — skip
            }
            else if(PositionSelect(pendingSymbol))
            {
                lastBeAttemptTime = TimeCurrent();
                double curSl  = PositionGetDouble(POSITION_SL);
                double curTp  = PositionGetDouble(POSITION_TP);
                long   position = PositionGetInteger(POSITION_TICKET);
                long   beDigits = (long)SymbolInfoInteger(pendingSymbol, SYMBOL_DIGITS);
                double bePoint  = SymbolInfoDouble(pendingSymbol, SYMBOL_POINT);
                double stopsLevel = (double)SymbolInfoInteger(pendingSymbol, SYMBOL_TRADE_STOPS_LEVEL) * bePoint;
                double freezeLevel = (double)SymbolInfoInteger(pendingSymbol, SYMBOL_TRADE_FREEZE_LEVEL) * bePoint;
                double triggerPrice = pendingBeTrigger;
                double currentPrice = (pendingDirection == "BUY")
                    ? SymbolInfoDouble(pendingSymbol, SYMBOL_BID)
                    : SymbolInfoDouble(pendingSymbol, SYMBOL_ASK);

                bool reached = false;
                if(pendingDirection == "BUY" && currentPrice >= triggerPrice)
                    reached = true;
                if(pendingDirection == "SELL" && currentPrice <= triggerPrice)
                    reached = true;

                if(reached && MathAbs(curSl - pendingEntry) > bePoint && curSl != 0)
                {
                    double newSl = NormalizeDouble(pendingEntry, (int)beDigits);
                    double newTp = NormalizeDouble(curTp, (int)beDigits);
                    double minDistance = MathMax(stopsLevel, freezeLevel);

                    if(pendingDirection == "BUY")
                    {
                        double maxSl = NormalizeDouble(currentPrice - minDistance, (int)beDigits);
                        if(newSl > maxSl)
                        {
                            Log("Break-even: SL too close, adjusting " + DoubleToString(newSl, (int)beDigits) + " to " + DoubleToString(maxSl, (int)beDigits));
                            newSl = maxSl;
                        }
                    }
                    else
                    {
                        double minSl = NormalizeDouble(currentPrice + minDistance, (int)beDigits);
                        if(newSl < minSl)
                        {
                            Log("Break-even: SL too close, adjusting " + DoubleToString(newSl, (int)beDigits) + " to " + DoubleToString(minSl, (int)beDigits));
                            newSl = minSl;
                        }
                    }

                    MqlTradeRequest modReq = {};
                    MqlTradeResult modRes = {};
                    modReq.action = TRADE_ACTION_SLTP;
                    modReq.symbol = pendingSymbol;
                    modReq.position = (ulong)position;
                    modReq.sl = newSl;
                    modReq.tp = newTp;
                    long filling = SymbolInfoInteger(pendingSymbol, SYMBOL_FILLING_MODE);
                    if((filling & SYMBOL_FILLING_FOK) != 0)
                        modReq.type_filling = ORDER_FILLING_FOK;
                    else if((filling & SYMBOL_FILLING_IOC) != 0)
                        modReq.type_filling = ORDER_FILLING_IOC;
                    else
                        modReq.type_filling = ORDER_FILLING_RETURN;
                    modReq.magic = MagicNumber;
                    Log("Break-even: pos=" + (string)position + " SL=" + DoubleToString(newSl, (int)beDigits) + " TP=" + DoubleToString(newTp, (int)beDigits));
                    if(!OrderSend(modReq, modRes))
                    {
                        int beErr = GetLastError();
                        Log("Break-even: OrderSend SLTP failed: " + (string)beErr + " retcode=" + (string)modRes.retcode);
                    }
                    else
                    {
                        if(PositionSelectByTicket((ulong)position))
                        {
                            double verifySl = PositionGetDouble(POSITION_SL);
                            if(MathAbs(verifySl - newSl) <= bePoint)
                            {
                                breakEvenApplied = true;
                                Log("Break-even applied at entry price=" + DoubleToString(newSl, (int)beDigits));
                                Comment("BREAK EVEN\nSL moved to entry");
                            }
                            else
                            {
                                Log("Break-even: SL not moved, verifySl=" + DoubleToString(verifySl, (int)beDigits) + " target=" + DoubleToString(newSl, (int)beDigits));
                            }
                        }
                    }
                }
            }
        }

        // After execution, check if position is still open
        if(currentState == STATE_EXECUTED)
        {
            if(!PositionSelect(pendingSymbol))
                currentState = STATE_DONE;
        }

        // Transition terminal states to cleanup
        if(currentState == STATE_ERROR || currentState == STATE_CANCELLED)
            currentState = STATE_DONE;

        if(currentState == STATE_DONE)
        {
            _cleanupTrade();
            currentState = STATE_IDLE;
        }
        return;
    }

    // IDLE state: poll Flask for armed trades
    string eaSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);
    string url = FlaskURL + "/api/ea/pending?symbol=" + eaSymbol + "&trade_id=" + UrlEncode(trackedTradeId);
    string response;
    bool ok = SendGetRequest(url, response);

    if(!ok)
    {
        Log("ERROR: Failed to poll Flask /api/ea/pending.");
        currentState = STATE_ERROR;
        Comment("FAILED\nConnection error");
        _cleanupTrade();
        currentState = STATE_DONE;
        return;
    }

    string tradeId       = Trim(ExtractJsonValue(response, "trade_id"));
    string status        = Trim(ExtractJsonValue(response, "status"));
    string requestedSymbol = Trim(ExtractJsonValue(response, "requested_symbol"));

    if(requestedSymbol != "" && requestedSymbol != ea_requested_symbol)
        ea_requested_symbol = requestedSymbol;

    string targetSymbol = Trim(ExtractJsonValue(response, "target_symbol"));
    string tradeSymbol  = Trim(ExtractJsonValue(response, "symbol"));

    if(tradeId == "" || status == "")
    {
        Log("[TradeLifecycle][EA_PENDING_EMPTY] tradeId=empty status=empty symbol=" + eaSymbol + " trackedTradeId=" + trackedTradeId);
        // Even if no trade armed, check for close requests
        string closeTicketStr = Trim(ExtractJsonValue(response, "close_ticket"));
        long closeTicket = 0;
        if(closeTicketStr != "" && closeTicketStr != "0")
            closeTicket = StringToInteger(closeTicketStr);
        if(closeTicket > 0)
        {
            string closeSymbol = Trim(ExtractJsonValue(response, "close_symbol"));
            if(closeSymbol == "")
                closeSymbol = _Symbol;
            EnsureSymbol(closeSymbol);
            ClosePositionByTicket(closeTicket, closeSymbol);
        }
        return;
    }

    if(targetSymbol != "" && targetSymbol != _Symbol)
    {
        if(EnsureSymbol(targetSymbol))
            ReportSymbolInfoFor(targetSymbol);
    }

    if(tradeSymbol != "" && tradeSymbol != _Symbol && tradeSymbol != targetSymbol)
    {
        if(EnsureSymbol(tradeSymbol))
            ReportSymbolInfoFor(tradeSymbol);
    }

    if(trackedTradeId != tradeId && status == "armed")
    {
        string direction    = Trim(ExtractJsonValue(response, "direction"));
        string candleTimeStr = Trim(ExtractJsonValue(response, "candle_time"));
        string timeframe    = Trim(ExtractJsonValue(response, "timeframe"));

        pendingLot    = StringToDouble(ExtractJsonValue(response, "lot"));
        pendingSl     = StringToDouble(ExtractJsonValue(response, "sl"));
        pendingTp     = StringToDouble(ExtractJsonValue(response, "tp"));
        pendingEntry  = StringToDouble(ExtractJsonValue(response, "entry"));
        pendingRiskDistance = MathAbs(pendingEntry - pendingSl);
        pendingBeRr   = StringToDouble(ExtractJsonValue(response, "be_rr"));
        pendingBeTrigger = StringToDouble(ExtractJsonValue(response, "be_trigger"));
        pendingManualSl    = StringToDouble(ExtractJsonValue(response, "manual_sl"));
        pendingRiskAmount  = StringToDouble(ExtractJsonValue(response, "risk_amount"));
        if(pendingRiskAmount <= 0) pendingRiskAmount = 310.0;
        pendingRrRatio     = StringToDouble(ExtractJsonValue(response, "rr_ratio"));
        if(pendingRrRatio <= 0) pendingRrRatio = 5.0;
        breakEvenApplied = false;
        pendingSymbol = (targetSymbol != "" ? targetSymbol : tradeSymbol);

        Log("[CandlePipeline][EA_RECEIVED] tradeId=" + tradeId + " symbol=" + pendingSymbol + " direction=" + direction +
            " candle_time=" + candleTimeStr + " timeframe=" + timeframe +
            " entry=" + DoubleToString(pendingEntry, (int)SymbolInfoInteger(pendingSymbol, SYMBOL_DIGITS)) +
            " sl=" + DoubleToString(pendingSl, (int)SymbolInfoInteger(pendingSymbol, SYMBOL_DIGITS)) +
            " manual_sl=" + DoubleToString(pendingManualSl, (int)SymbolInfoInteger(pendingSymbol, SYMBOL_DIGITS)) +
            " tp=" + DoubleToString(pendingTp, (int)SymbolInfoInteger(pendingSymbol, SYMBOL_DIGITS)) +
            " risk=" + DoubleToString(pendingRiskAmount, 2) + " rr=" + DoubleToString(pendingRrRatio, 2));

        trackedTradeId   = tradeId;
        pendingTradeId   = tradeId;
        pendingDirection = direction;

        int tf_minutes = 15;
        if(timeframe == "M1") tf_minutes = 1;
        else if(timeframe == "M5") tf_minutes = 5;
        else if(timeframe == "M15") tf_minutes = 15;
        else if(timeframe == "M30") tf_minutes = 30;
        else if(timeframe == "H1") tf_minutes = 60;
        else if(timeframe == "H4") tf_minutes = 240;
        else if(timeframe == "D1") tf_minutes = 1440;

        string candleCloseStr = Trim(ExtractJsonValue(response, "candle_close_unix"));
        string digitsOnly = "";
        for(int i = 0; i < StringLen(candleCloseStr); i++)
        {
            ushort ch = StringGetCharacter(candleCloseStr, i);
            if(ch >= '0' && ch <= '9')
                digitsOnly += StringSubstr(candleCloseStr, i, 1);
        }
        candleCloseTime = 0;
        if(digitsOnly != "")
            candleCloseTime = (datetime)StringToInteger(digitsOnly);

        Log("========== TIMING DEBUG ==========");
        Log("Flask candle_close_unix RAW = " + candleCloseStr);
        Log("Parsed candleCloseTime      = " + (string)candleCloseTime);
        Log("candleCloseTime             = " + TimeToString(candleCloseTime, TIME_DATE|TIME_SECONDS));
        Log("TimeCurrent()               = " + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS));
        Log("TimeTradeServer()           = " + TimeToString(TimeTradeServer(), TIME_DATE|TIME_SECONDS));
        Log("TimeLocal()                 = " + TimeToString(TimeLocal(), TIME_DATE|TIME_SECONDS));
        Log("TimeGMT()                   = " + TimeToString(TimeGMT(), TIME_DATE|TIME_SECONDS));
        Log("DIFFERENCE                  = " + (string)(candleCloseTime - TimeCurrent()) + " seconds");
        Log("==================================");

        if(candleCloseTime <= 0)
        {
            datetime now = TimeCurrent();
            MqlDateTime dt;
            TimeToStruct(now, dt);
            dt.sec = 0;

            int safety = 0;
            do
            {
                if(tf_minutes < 60)
                {
                    int remainder = dt.min % tf_minutes;
                    if(remainder == 0)
                        dt.min += tf_minutes;
                    else
                        dt.min = ((dt.min / tf_minutes) + 1) * tf_minutes;
                }
                else if(tf_minutes >= 1440)
                {
                    dt.hour = 0;
                    dt.min = 0;
                    dt.day++;
                }
                else
                {
                    int tf_hours = tf_minutes / 60;
                    int remainder = dt.hour % tf_hours;
                    if(remainder == 0 && dt.min == 0)
                        dt.hour += tf_hours;
                    else
                    {
                        dt.hour = ((dt.hour / tf_hours) + 1) * tf_hours;
                        dt.min = 0;
                    }
                }

                if(dt.min >= 60) { dt.min -= 60; dt.hour++; }
                if(dt.hour >= 24) { dt.hour -= 24; dt.day++; }
                candleCloseTime = StructToTime(dt);
                safety++;
            }
            while(candleCloseTime <= now && safety < 50);
        }

        datetime now = TimeCurrent();
        Log("ARMED tf=" + timeframe + " wait=" + (string)(candleCloseTime - now) + "s tradeId=" + tradeId
            + " lot=" + (string)pendingLot + " SL=" + (string)pendingSl + " TP=" + (string)pendingTp
            + " beRr=" + (string)pendingBeRr + " beTrigger=" + (string)pendingBeTrigger);

        currentState  = STATE_ARMED;
        armedTime     = TimeCurrent();
        armedSymbol   = pendingSymbol;
        armedTfMinutes = tf_minutes;
        armedBarTime  = iTime(armedSymbol, _PeriodToTf(tf_minutes), 0);

        EnsureSymbol(armedSymbol);

        Log("======================================");
        Log("TRADE ARMED — all params pre-loaded");
        Log("Direction: " + direction);
        Log("Trade ID: " + tradeId);
        Log("Waiting for candle close...");
        Log("======================================");
    }

    if(currentState == STATE_ARMED && candleCloseTime > 0)
    {
        datetime now = TimeGMT();
        datetime currentBarTime = iTime(armedSymbol, _PeriodToTf(armedTfMinutes), 0);
        bool barChanged  = (currentBarTime != armedBarTime);
        bool timeReached = (now >= candleCloseTime);

        long barAgeSeconds = 0;
        if(currentBarTime > 0)
            barAgeSeconds = (long)(now - currentBarTime);
        long maxExpectedAge = (long)armedTfMinutes * 2 * 60;
        bool staleBar = (barAgeSeconds > maxExpectedAge);
        if(staleBar)
        {
            Log("CHECK STALE BAR tradeId=" + trackedTradeId + " barAge=" + (string)barAgeSeconds + "s now=" + TimeToString(now) + " barOpen=" + TimeToString(currentBarTime));
            LogTimeDiagnostics(armedSymbol, _PeriodToTf(armedTfMinutes));
        }

        long remainingSeconds = (long)candleCloseTime - (long)now;

        Log("CHECK tradeId=" + trackedTradeId
            + " barChanged=" + (string)barChanged
            + " timeReached=" + (string)timeReached
            + " now=" + TimeToString(now, TIME_DATE|TIME_SECONDS)
            + " close=" + TimeToString(candleCloseTime, TIME_DATE|TIME_SECONDS)
            + " remaining=" + (string)remainingSeconds + "s"
            + " staleBar=" + (string)staleBar
            + " currentBar=" + TimeToString(currentBarTime)
            + " previousBar=" + TimeToString(iTime(armedSymbol, _PeriodToTf(armedTfMinutes), 1))
            + " serverTime=" + TimeToString(TimeTradeServer())
            + " localTime=" + TimeToString(TimeLocal())
            + " gmtTime=" + TimeToString(TimeGMT()));

        if(staleBar)
        {
            Log("STALE_BAR_BLOCKED tradeId=" + trackedTradeId + " — execution blocked, waiting for synchronized market data");
            ReportExecutionDetailed(pendingTradeId, "stale_bar", 1, "Stale candle data detected, execution blocked", 0, 0, 0, 0, 0);
            int remaining = (int)(candleCloseTime - now);
            Comment("STALE BAR\nExecution Blocked\nCheck data feed\n" + armedSymbol + "\nBar age: " + (string)barAgeSeconds + "s");
        }
        else if(barChanged || timeReached)
        {
            Log("EXECUTE: candle closed — executing OrderSend locally");
            ExecuteTradeLocal();
        }
        else
        {
            int remaining = (int)(candleCloseTime - now);
            Comment("ARMED " + pendingDirection + " " + armedSymbol + "\n",
                    "Closes in: " + (string)remaining + " seconds\n",
                    "Lot=" + (string)pendingLot + " SL=" + (string)pendingSl + " TP=" + (string)pendingTp);
        }
    }
}

void ExecuteTradeLocal()
{
    if(pendingTradeId == "" || pendingSymbol == "")
        return;

    pendingTradeId = Trim(pendingTradeId);
    string execSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);

    if(!EnsureSymbol(execSymbol))
    {
        Log("ExecuteTradeLocal: Failed to select symbol " + execSymbol);
        ReportExecutionDetailed(pendingTradeId, "error", 1, "Failed to select symbol in Market Watch", 0, 0, 0, 0, 0);
        currentState = STATE_ERROR;
        Comment("FAILED\nSelect symbol in Market Watch");
        return;
    }

    MqlTick tick;
    if(!SymbolInfoTick(execSymbol, tick) || tick.bid <= 0 || tick.ask <= 0)
    {
        Log("ExecuteTradeLocal: No tick data for " + execSymbol);
        ReportExecutionDetailed(pendingTradeId, "error", 1, "No market data for symbol", 0, 0, 0, 0, 0);
        currentState = STATE_ERROR;
        Comment("FAILED\nNo market data");
        return;
    }

    MqlTradeRequest request = {};
    MqlTradeResult result = {};

    long    digits    = (long)SymbolInfoInteger(execSymbol, SYMBOL_DIGITS);
    double  point     = SymbolInfoDouble(execSymbol, SYMBOL_POINT);
    long    filling   = SymbolInfoInteger(execSymbol, SYMBOL_FILLING_MODE);
    double  slLevel   = (double)SymbolInfoInteger(execSymbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
    double  lotStep   = SymbolInfoDouble(execSymbol, SYMBOL_VOLUME_STEP);
    double  lotMin    = SymbolInfoDouble(execSymbol, SYMBOL_VOLUME_MIN);
    double  lotMax    = SymbolInfoDouble(execSymbol, SYMBOL_VOLUME_MAX);

    ENUM_ORDER_TYPE_FILLING fillMode;
    if((filling & SYMBOL_FILLING_FOK) != 0)
        fillMode = ORDER_FILLING_FOK;
    else if((filling & SYMBOL_FILLING_IOC) != 0)
        fillMode = ORDER_FILLING_IOC;
    else
        fillMode = ORDER_FILLING_RETURN;

    double lot = pendingLot;
    if(lotStep > 0)
        lot = MathFloor(lot / lotStep) * lotStep;
    lot = MathMax(lot, lotMin);
    lot = MathMin(lot, lotMax);

    double execEntry = (pendingDirection == "BUY") ? tick.ask : tick.bid;
    double reqSl = (pendingManualSl > 0) ? pendingManualSl : pendingSl;
    double reqTp = pendingTp;
    double riskDistance = MathAbs(execEntry - reqSl);

    if(riskDistance > 0 && pendingRrRatio > 0)
    {
        if(pendingDirection == "BUY")
        {
            reqTp = NormalizeDouble(execEntry + riskDistance * pendingRrRatio, (int)digits);
        }
        else
        {
            reqTp = NormalizeDouble(execEntry - riskDistance * pendingRrRatio, (int)digits);
        }
    }

    if(riskDistance > 0 && pendingRiskAmount > 0)
    {
        double tickValue = SymbolInfoDouble(execSymbol, SYMBOL_TRADE_TICK_VALUE);
        double tickSize = SymbolInfoDouble(execSymbol, SYMBOL_TRADE_TICK_SIZE);
        double lossPerLot = 0;

        if(tickSize > 0 && tickValue > 0)
        {
            double distanceTicks = riskDistance / tickSize;
            lossPerLot = tickValue * distanceTicks;
        }

        if(lossPerLot <= 0 && point > 0 && tickValue > 0)
        {
            double distancePoints = riskDistance / point;
            lossPerLot = tickValue * distancePoints;
        }

        if(lossPerLot > 0)
        {
            double calcLot = pendingRiskAmount / lossPerLot;
            calcLot = MathFloor(calcLot / lotStep) * lotStep;
            calcLot = MathMax(calcLot, lotMin);
            calcLot = MathMin(calcLot, lotMax);
            lot = calcLot;
        }
    }

    double minDist = MathMax(slLevel, point * 10);
    double currentPrice = (pendingDirection == "BUY") ? tick.ask : tick.bid;
    double originalSl = reqSl;
    double originalTp = reqTp;

    Log("ExecuteTradeLocal: bid=" + DoubleToString(tick.bid, (int)digits) + " ask=" + DoubleToString(tick.ask, (int)digits) + " slLevel=" + (string)slLevel + " minDist=" + DoubleToString(minDist, (int)digits));

    Log("ExecuteTradeLocal: ManualSL=" + DoubleToString(pendingManualSl, (int)digits) + " execEntry=" + DoubleToString(execEntry, (int)digits) + " riskDist=" + DoubleToString(riskDistance, (int)digits) + " riskAmt=" + DoubleToString(pendingRiskAmount, 2) + " RR=" + DoubleToString(pendingRrRatio, 2) + " calcLot=" + DoubleToString(lot, 2));

    if(pendingDirection == "BUY")
    {
        if(reqTp <= currentPrice + minDist)
        {
            Log("ExecuteTradeLocal: Broker TP adjustment pendingTp=" + DoubleToString(reqTp, (int)digits) + " -> " + DoubleToString(currentPrice + minDist, (int)digits));
            reqTp = NormalizeDouble(currentPrice + minDist, (int)digits);
        }
    }
    else  // SELL
    {
        if(reqTp >= currentPrice - minDist)
        {
            Log("ExecuteTradeLocal: Broker TP adjustment pendingTp=" + DoubleToString(reqTp, (int)digits) + " -> " + DoubleToString(currentPrice - minDist, (int)digits));
            reqTp = NormalizeDouble(currentPrice - minDist, (int)digits);
        }
    }

    long freezeLevel = (long)SymbolInfoInteger(execSymbol, SYMBOL_TRADE_FREEZE_LEVEL);
    double freezeDist = freezeLevel * point;
    double slippage = MathAbs(execEntry - pendingEntry);
    double spread = tick.ask - tick.bid;

    Log("ExecuteTradeLocal: ======== FULL ORDER PARAMS ========");
    Log("ExecuteTradeLocal: Symbol=" + execSymbol + " Direction=" + pendingDirection);
    Log("ExecuteTradeLocal: Bid=" + DoubleToString(tick.bid, (int)digits) + " Ask=" + DoubleToString(tick.ask, (int)digits));
    Log("ExecuteTradeLocal: Entry(pending)=" + DoubleToString(pendingEntry, (int)digits) + " Entry(exec)=" + DoubleToString(execEntry, (int)digits));
    Log("ExecuteTradeLocal: SL(req)=" + DoubleToString(reqSl, (int)digits) + " TP(req)=" + DoubleToString(reqTp, (int)digits));
    Log("ExecuteTradeLocal: SL(orig)=" + DoubleToString(originalSl, (int)digits) + " TP(orig)=" + DoubleToString(originalTp, (int)digits));
    Log("ExecuteTradeLocal: Volume=" + (string)lot + " LotStep=" + DoubleToString(lotStep, 2) + " LotMin=" + DoubleToString(lotMin, 2) + " LotMax=" + DoubleToString(lotMax, 2));
    Log("ExecuteTradeLocal: StopsLevel=" + (string)slLevel + " FreezeLevel=" + (string)freezeDist + " MinDist=" + DoubleToString(minDist, (int)digits));
    Log("ExecuteTradeLocal: Digits=" + (string)digits + " Point=" + DoubleToString(point, (int)digits));
    Log("ExecuteTradeLocal: FillingMode=" + (string)fillMode + " Deviation=" + (string)Deviation);
    Log("ExecuteTradeLocal: Slippage=" + DoubleToString(slippage, (int)digits) + " Spread=" + DoubleToString(spread, (int)digits));
    Log("ExecuteTradeLocal: =======================================");

    // Validate SL direction for SELL: SL must be ABOVE entry
    if(pendingDirection == "SELL" && reqSl <= execEntry)
    {
        Log("ExecuteTradeLocal: INVALID SL for SELL - SL must be above entry. Rejecting trade.");
        ReportExecutionDetailed(pendingTradeId, "error", 1, "Invalid SL for SELL: SL must be above entry", 0, 0, execEntry, 0, 0);
        currentState = STATE_ERROR;
        Comment("FAILED\nInvalid SL");
        return;
    }
    // Validate SL direction for BUY: SL must be BELOW entry
    if(pendingDirection == "BUY" && reqSl >= execEntry)
    {
        Log("ExecuteTradeLocal: INVALID SL for BUY - SL must be below entry. Rejecting trade.");
        ReportExecutionDetailed(pendingTradeId, "error", 1, "Invalid SL for BUY: SL must be below entry", 0, 0, execEntry, 0, 0);
        currentState = STATE_ERROR;
        Comment("FAILED\nInvalid SL");
        return;
    }

    // Validate TP direction for SELL: TP must be BELOW entry
    if(pendingDirection == "SELL" && reqTp >= execEntry)
    {
        Log("ExecuteTradeLocal: INVALID TP for SELL - TP must be below entry. Adjusting TP.");
        reqTp = NormalizeDouble(execEntry - minDist, (int)digits);
    }
    // Validate TP direction for BUY: TP must be ABOVE entry
    if(pendingDirection == "BUY" && reqTp <= execEntry)
    {
        Log("ExecuteTradeLocal: INVALID TP for BUY - TP must be above entry. Adjusting TP.");
        reqTp = NormalizeDouble(execEntry + minDist, (int)digits);
    }

    request.action      = TRADE_ACTION_DEAL;
    request.symbol      = execSymbol;
    request.volume      = lot;
    request.sl          = reqSl;
    request.tp          = reqTp;
    request.deviation   = Deviation;
    request.magic       = MagicNumber;
    request.comment     = "ExecutionBot " + execSymbol + " " + pendingDirection;
    request.type_time   = ORDER_TIME_GTC;
    request.type_filling = fillMode;

    if(pendingDirection == "BUY")
    {
        request.type  = ORDER_TYPE_BUY;
        request.price = tick.ask;
    }
    else
    {
        request.type  = ORDER_TYPE_SELL;
        request.price = tick.bid;
    }

    string commentStr = "ExecutionBot " + execSymbol + " " + pendingDirection;
    Log("OrderSend " + execSymbol + " " + pendingDirection + " lot=" + (string)lot
        + " SL=" + DoubleToString(reqSl, (int)digits) + " TP=" + DoubleToString(reqTp, (int)digits)
        + " dev=" + (string)Deviation + " magic=" + (string)MagicNumber + " fill=" + (string)fillMode);

    Log("[TradeLifecycle][EXECUTION_ATTEMPT] tradeId=" + pendingTradeId + " symbol=" + execSymbol + " direction=" + pendingDirection +
        " entry=" + DoubleToString(execEntry, (int)digits) + " sl=" + DoubleToString(reqSl, (int)digits) +
        " tp=" + DoubleToString(reqTp, (int)digits) + " lot=" + DoubleToString(lot, 2) +
        " manual_sl=" + DoubleToString(pendingManualSl, (int)digits) + " risk=" + DoubleToString(pendingRiskAmount, 2));

    long tradeMode = SymbolInfoInteger(execSymbol, SYMBOL_TRADE_MODE);
    long terminalTradeAllowed = TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
    Log("ExecuteTradeLocal: tradeMode=" + (string)tradeMode + " terminalTradeAllowed=" + (string)terminalTradeAllowed
        + " serverTime=" + TimeToString(TimeCurrent()));

    if(tradeMode == 0 || (pendingDirection == "BUY" && tradeMode == 2) || (pendingDirection == "SELL" && tradeMode == 1))
    {
        Log("ExecuteTradeLocal: MARKET CLOSED or direction not allowed. tradeMode=" + (string)tradeMode + " direction=" + pendingDirection);
        ReportExecutionDetailed(pendingTradeId, "market_closed", (int)TRADE_RETCODE_MARKET_CLOSED, "Market closed or trading not allowed for this direction", 0, 0, execEntry, slippage, spread);
        currentState = STATE_ERROR;
        Comment("FAILED\nMarket closed");
        return;
    }

    int sendAttempts = 0;
    int maxAttempts = 3;
    double trySl = reqSl;
    double tryTp = reqTp;
    bool orderSuccess = false;
    int lastErr = 0;
    int lastRetcode = 0;
    string lastComment = "";
    ulong lastOrder = 0;
    ulong lastDeal = 0;

    while(sendAttempts < maxAttempts && !orderSuccess)
    {
        request.sl = trySl;
        request.tp = tryTp;

        if(OrderSend(request, result))
        {
            orderSuccess = true;
            lastRetcode = (int)result.retcode;
            lastComment = result.comment;
            lastOrder = result.order;
            lastDeal = result.deal;
            break;
        }

        lastErr = GetLastError();
        lastRetcode = (int)result.retcode;
        lastComment = result.comment;
        lastOrder = result.order;
        lastDeal = result.deal;
        sendAttempts++;

        Log("OrderSend FAILED attempt=" + (string)sendAttempts + " err=" + (string)lastErr
            + " retcode=" + (string)lastRetcode + " comment=" + lastComment
            + " order=" + (string)lastOrder + " deal=" + (string)lastDeal);

        if(lastErr == 4756 && sendAttempts < maxAttempts)
        {
            Log("OrderSend 4756 retry " + (string)sendAttempts + "/" + (string)maxAttempts + " keeping original SL/TP");
            Sleep(500);
            continue;
        }

        if(lastErr == TRADE_RETCODE_MARKET_CLOSED || lastRetcode == TRADE_RETCODE_MARKET_CLOSED)
        {
            Log("ExecuteTradeLocal: MARKET CLOSED (retcode=" + (string)lastRetcode + "). Not retrying.");
            ReportExecutionDetailed(pendingTradeId, "market_closed", (int)TRADE_RETCODE_MARKET_CLOSED, "Market closed", (long)lastOrder, (long)lastDeal, execEntry, slippage, spread);
            currentState = STATE_ERROR;
            Comment("FAILED\nMarket closed");
            return;
        }

        ReportExecutionDetailed(pendingTradeId, "error", lastErr, "OrderSend failed", 0, 0, execEntry, slippage, spread);
        currentState = STATE_ERROR;
        Comment("FAILED\nOrderSend failed err=" + (string)lastErr);
        return;
    }

    if(!orderSuccess)
    {
        Log("[TradeLifecycle][EXECUTION_FAILED] tradeId=" + pendingTradeId + " retcode=" + (string)lastRetcode + " comment=" + lastComment + " order=" + (string)lastOrder + " deal=" + (string)lastDeal);
        Log("OrderSend failed after " + (string)maxAttempts + " attempts. Last err=" + (string)lastErr
            + " retcode=" + (string)lastRetcode + " comment=" + lastComment);
        ReportExecutionDetailed(pendingTradeId, "error", 4756, "OrderSend failed after retries", 0, 0, execEntry, slippage, spread);
        currentState = STATE_ERROR;
        Comment("FAILED\nOrderSend failed after retries");
        return;
    }

    Log("[TradeLifecycle][EXECUTION_SUCCESS] tradeId=" + pendingTradeId + " ticket=" + (string)lastOrder + " deal=" + (string)lastDeal +
        " entry=" + DoubleToString(execEntry, (int)digits) + " sl=" + DoubleToString(reqSl, (int)digits) +
        " tp=" + DoubleToString(reqTp, (int)digits) + " volume=" + DoubleToString(lot, 2) +
        " retcode=" + (string)lastRetcode);
    
    Log("OrderSend SUCCESS retcode=" + (string)lastRetcode + " comment=" + lastComment
        + " order=" + (string)lastOrder + " deal=" + (string)lastDeal);

    if(lastRetcode == TRADE_RETCODE_DONE)
    {
        ReportExecutionDetailed(pendingTradeId, "executed", lastRetcode, "OK",
                                (long)lastOrder, (long)lastDeal, execEntry, slippage, spread);
        currentState = STATE_EXECUTED;
        Comment("EXECUTED\n" + commentStr + "\nEntry=" + DoubleToString(execEntry, (int)digits)
                + "\nSlippage=" + DoubleToString(slippage, (int)digits));

        // Detect position on next timer tick, not now
    }
    else
    {
        ReportExecutionDetailed(pendingTradeId, "error", lastRetcode, lastComment,
                                (long)lastOrder, (long)lastDeal, execEntry, slippage, spread);
        currentState = STATE_ERROR;
        Comment("FAILED\n" + lastComment);
    }
}

void ClosePositionByTicket(long ticket, string symbol)
{
    if(!EnsureSymbol(symbol))
        return;

    // Select position by TICKET, not by symbol, to avoid selecting wrong position
    if(!PositionSelectByTicket((ulong)ticket))
    {
        Log("ClosePosition: PositionSelectByTicket failed for ticket=" + (string)ticket);
        return;
    }

    // Verify the selected position matches
    string actualSymbol = PositionGetString(POSITION_SYMBOL);
    if(actualSymbol != symbol)
    {
        Log("ClosePosition: Ticket " + (string)ticket + " symbol mismatch: expected=" + symbol + " actual=" + actualSymbol);
        return;
    }

    long posType = PositionGetInteger(POSITION_TYPE);
    double posVolume = PositionGetDouble(POSITION_VOLUME);
    double posPriceOpen = PositionGetDouble(POSITION_PRICE_OPEN);
    long posTicket = (long)PositionGetInteger(POSITION_TICKET);

    MqlTick tick;
    if(!SymbolInfoTick(symbol, tick))
    {
        Log("ClosePosition: No tick data for " + symbol);
        return;
    }

    MqlTradeRequest request = {};
    MqlTradeResult result = {};

    request.action = TRADE_ACTION_DEAL;
    request.symbol = symbol;
    request.volume = posVolume;
    request.position = (ulong)posTicket;
    request.deviation = Deviation;
    request.magic = MagicNumber;
    request.comment = "Close by EA";
    request.type_time = ORDER_TIME_GTC;
    long fillingMode = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
    if((fillingMode & SYMBOL_FILLING_FOK) != 0)
        request.type_filling = ORDER_FILLING_FOK;
    else if((fillingMode & SYMBOL_FILLING_IOC) != 0)
        request.type_filling = ORDER_FILLING_IOC;
    else
        request.type_filling = ORDER_FILLING_RETURN;

    double closePrice = 0;
    double pnl = 0;

    if(posType == POSITION_TYPE_BUY)
    {
        request.type = ORDER_TYPE_SELL;
        request.price = tick.bid;
        closePrice = tick.bid;
        pnl = (tick.bid - posPriceOpen) * posVolume * _Point * 10;
    }
    else if(posType == POSITION_TYPE_SELL)
    {
        request.type = ORDER_TYPE_BUY;
        request.price = tick.ask;
        closePrice = tick.ask;
        pnl = (posPriceOpen - tick.ask) * posVolume * _Point * 10;
    }
    else
    {
        Log("ClosePosition: Unknown position type: " + (string)posType);
        return;
    }

    Log("ClosePosition: Sending close ticket=" + (string)posTicket
        + " symbol=" + symbol + " type=" + (string)posType
        + " volume=" + (string)posVolume + " price=" + DoubleToString(closePrice, 5)
        + " pnl=" + DoubleToString(pnl, 2));

    if(!OrderSend(request, result))
    {
        int err = GetLastError();
        Log("ClosePosition: OrderSend FAILED retcode=" + (string)result.retcode
            + " err=" + (string)err + " comment=" + result.comment);
    }
    else
    {
        Log("ClosePosition: OrderSend retcode=" + (string)result.retcode
            + " comment=" + result.comment + " deal=" + (string)result.deal);
        string payload = StringFormat(
            "{\"ticket\":%d,\"symbol\":\"%s\",\"status\":\"closed\",\"price\":%.5f,\"pnl\":%.2f,\"retcode\":%d,\"comment\":\"%s\"}",
            (int)posTicket, symbol, closePrice, pnl, (int)result.retcode, result.comment
        );
        string response;
        SendPostRequest(FlaskURL + "/api/ea/report_close", payload, response);
    }
}

bool SendPostRequest(string url, string jsonPayload, string &response)
{
    uchar postData[];
    int postSize = StringToCharArray(jsonPayload, postData);
    if(postSize > 0)
        ArrayResize(postData, postSize - 1);

    uchar result[];
    string headers = "Content-Type: application/json\r\n";
    string headers_out;

    ResetLastError();
    int res = WebRequest("POST", url, headers, 10000, postData, result, headers_out);
    int err = GetLastError();

    int len = 0;
    for(int i = 0; i < ArraySize(result); i++)
    {
        if(result[i] == 0) break;
        len++;
    }
    response = CharArrayToString(result, 0, len);

    if(res >= 200 && res < 300)
        return true;

    Log("POST failed: " + url + " HTTP=" + (string)res + " MT5_err=" + (string)err);
    return false;
}

bool SendGetRequest(string url, string &response)
{
    uchar empty_data[];
    uchar result[];
    string headers = "";
    string headers_out;

    ResetLastError();
    int res = WebRequest("GET", url, headers, 10000, empty_data, result, headers_out);
    int err = GetLastError();

    int len = 0;
    for(int i = 0; i < ArraySize(result); i++)
    {
        if(result[i] == 0) break;
        len++;
    }
    response = CharArrayToString(result, 0, len);

    if(res >= 200 && res < 300)
        return true;

    Log("GET failed: " + url + " HTTP=" + (string)res + " MT5_err=" + (string)err);
    return false;
}

string UrlEncode(string value)
{
    StringReplace(value, ":", "%3A");
    StringReplace(value, " ", "%20");
    StringReplace(value, "+", "%2B");
    StringReplace(value, "#", "%23");
    StringReplace(value, "%", "%25");
    return value;
}

string ExtractJsonValue(string json, string key)
{
    // NOTE: Simple parser — handles flat key:value pairs (strings and numbers).
    // Does NOT handle: booleans, null, arrays, nested objects, escaped quotes.
    // Replace with a proper JSON library for production.
    string search = "\"" + key + "\":";
    int pos = StringFind(json, search);
    if(pos < 0) return "";

    pos += StringLen(search);

    while(pos < StringLen(json) && (json[pos] == ' ' || json[pos] == '\t'))
        pos++;

    if(pos >= StringLen(json)) return "";

    if(json[pos] == '\"')
    {
        pos++;
        int end = StringFind(json, "\"", pos);
        if(end < 0) return "";
        return StringSubstr(json, pos, end - pos);
    }

    string num = "";
    while(pos < StringLen(json) &&
          ((json[pos] >= '0' && json[pos] <= '9') ||
           json[pos] == '.' || json[pos] == '-' || json[pos] == 'e' || json[pos] == 'E'))
    {
        num += ShortToString(json[pos]);
        pos++;
    }
    return num;
}

string Trim(string value)
{
    int start = 0;
    int end = StringLen(value) - 1;
    while(start <= end && (value[start] == ' ' || value[start] == '\t' || value[start] == '\n' || value[start] == '\r'))
        start++;
    while(end >= start && (value[end] == ' ' || value[end] == '\t' || value[end] == '\n' || value[end] == '\r'))
        end--;
    if(end < start) return "";
    return StringSubstr(value, start, end - start + 1);
}

void ReportExecutionDetailed(string tradeId, string status, int retcode, string comment,
                             long ticket, long deal, double entry, double slippage, double spread)
{
    if(tradeId == "") return;

    string payload = StringFormat(
        "{\"trade_id\":\"%s\",\"status\":\"%s\",\"ticket\":%d,\"deal\":%d,"
        "\"entry\":%.8f,\"slippage\":%.8f,\"spread\":%.8f,\"retcode\":%d,"
        "\"comment\":\"%s\"}",
        tradeId, status, (int)ticket, (int)deal, entry, slippage, spread, retcode, comment
    );

    string response;
    string url = FlaskURL + "/api/ea/report_execution";
    SendPostRequest(url, payload, response);
}

void ReportPosition()
{
    if(trackedTradeId == "") return;

    string posSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);
    if(!PositionSelect(posSymbol))
        return;

    double volume = PositionGetDouble(POSITION_VOLUME);
    double price = PositionGetDouble(POSITION_PRICE_OPEN);
    double sl = PositionGetDouble(POSITION_SL);
    double tp = PositionGetDouble(POSITION_TP);
    double profit = PositionGetDouble(POSITION_PROFIT);
    string symbol = PositionGetString(POSITION_SYMBOL);
    long type = PositionGetInteger(POSITION_TYPE);
    string direction = (type == POSITION_TYPE_BUY) ? "BUY" : "SELL";
    long ticket = PositionGetInteger(POSITION_TICKET);

    static string lastPositionReport = "";
    long posDigits = MathMax((long)SymbolInfoInteger(symbol, SYMBOL_DIGITS), 0);
    string fingerprint = symbol + (string)ticket + DoubleToString(volume, 2) + DoubleToString(price, (int)posDigits) + DoubleToString(sl, (int)posDigits) + DoubleToString(tp, (int)posDigits) + DoubleToString(profit, 2);
    if(fingerprint == lastPositionReport)
        return;
    lastPositionReport = fingerprint;

    string payload = StringFormat(
        "{\"ticket\":%d,\"symbol\":\"%s\",\"direction\":\"%s\",\"lot\":%.2f,\"entry\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"profit\":%.2f}",
        (int)ticket, symbol, direction, volume, price, sl, tp, profit
    );

    string response;
    string url = FlaskURL + "/api/ea/report_position";
    SendPostRequest(url, payload, response);
}

void ReportAccount()
{
    long login = AccountInfoInteger(ACCOUNT_LOGIN);
    string server = AccountInfoString(ACCOUNT_SERVER);
    string company = AccountInfoString(ACCOUNT_COMPANY);
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    double profit = AccountInfoDouble(ACCOUNT_PROFIT);
    double margin = AccountInfoDouble(ACCOUNT_MARGIN);
    double margin_level = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);

    string payload = StringFormat(
        "{\"login\":%d,\"server\":\"%s\",\"company\":\"%s\",\"balance\":%.2f,\"equity\":%.2f,\"profit\":%.2f,\"margin\":%.2f,\"margin_level\":%.2f}",
        (int)login, server, company, balance, equity, profit, margin, margin_level
    );

    string response;
    string url = FlaskURL + "/api/ea/report_account";
    SendPostRequest(url, payload, response);
}

void ReportMarket()
{
    MqlTick tick;
    string current = (armedSymbol != "" ? armedSymbol : _Symbol);

    if(!SymbolInfoTick(current, tick) || tick.bid <= 0 || tick.ask <= 0)
    {
        if(current != _Symbol && !SymbolInfoTick(_Symbol, tick))
            return;
        current = _Symbol;
    }

    string response;
    string url = FlaskURL + "/api/ea/report_market";
    string combinedKey = "";

    // Report chart symbol price (dedup)
    long curDigits = MathMax((long)SymbolInfoInteger(current, SYMBOL_DIGITS), 0);
    string key1 = current + ":" + DoubleToString(tick.bid, (int)curDigits) + ":" + DoubleToString(tick.ask, (int)curDigits);
    if(key1 != lastMarketReport)
    {
        long synced = SeriesInfoInteger(current, 0, SERIES_SYNCHRONIZED);
        string payload = StringFormat("{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f,\"tick_time\":%d,\"series_synced\":%d}",
            current, tick.bid, tick.ask, (long)tick.time, synced);
        SendPostRequest(url, payload, response);
        lastMarketReport = key1;
    }

    // Report armed symbol (dedup via prefix)
    if(armedSymbol != "" && armedSymbol != _Symbol && armedSymbol != current)
    {
        MqlTick tick2;
        if(SymbolInfoTick(armedSymbol, tick2) && tick2.bid > 0 && tick2.ask > 0)
        {
            string key2 = armedSymbol + ":" + DoubleToString(tick2.bid, (int)MathMax((long)SymbolInfoInteger(armedSymbol, SYMBOL_DIGITS), 0)) + ":" + DoubleToString(tick2.ask, (int)MathMax((long)SymbolInfoInteger(armedSymbol, SYMBOL_DIGITS), 0));
            string payload2 = StringFormat("{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f}",
                armedSymbol, tick2.bid, tick2.ask);
            SendPostRequest(url, payload2, response);
        }
    }

    // Report requested symbol
    string reqSym = ea_requested_symbol;
    if(reqSym != "" && reqSym != _Symbol && reqSym != current && reqSym != armedSymbol)
    {
        MqlTick tick3;
        if(SymbolInfoTick(reqSym, tick3) && tick3.bid > 0 && tick3.ask > 0)
        {
            string payload3 = StringFormat("{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f}",
                reqSym, tick3.bid, tick3.ask);
            SendPostRequest(url, payload3, response);
        }
    }
}

void ReportTick(string symbol, double bid, double ask)
{
    string payload = StringFormat(
        "{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f}",
        symbol, bid, ask
    );
    string response;
    string url = FlaskURL + "/api/ea/report_market";
    SendPostRequest(url, payload, response);
}

void ReportTickFor(string symbol)
{
    if(symbol == "")
        return;
    MqlTick tick;
    if(!SymbolInfoTick(symbol, tick) || tick.bid <= 0 || tick.ask <= 0)
        return;
    ReportTick(symbol, tick.bid, tick.ask);
}

void ReportSymbolInfo()
{
    ReportSymbolInfoFor(_Symbol);
}

void ReportSymbolInfoFor(string symbol)
{
    if(symbol == "" || !EnsureSymbol(symbol))
    {
        Log("ReportSymbolInfoFor: symbol '" + symbol + "' not available on broker");
        return;
    }

    long   digits       = SymbolInfoInteger(symbol, SYMBOL_DIGITS);
    double point        = SymbolInfoDouble(symbol, SYMBOL_POINT);
    double vol_min      = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
    double vol_max      = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
    double vol_step     = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
    double tick_value   = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
    double stops_level  = (double)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
    double freeze_level = (double)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);
    long   filling      = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
    long   visible      = SymbolInfoInteger(symbol, SYMBOL_VISIBLE);
    long   trade_mode   = SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
    double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
    double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);

    Log("[SymbolDebug] ChartSymbol=" + _Symbol + " Requested=" + symbol + " Selected=" + (string)SymbolInfoInteger(symbol, SYMBOL_SELECT)
        + " Digits=" + (string)digits + " Point=" + DoubleToString(point, 8)
        + " Bid=" + DoubleToString(bid, 5) + " Ask=" + DoubleToString(ask, 5)
        + " TradeMode=" + (string)trade_mode
        + " VolMin=" + DoubleToString(vol_min, 2) + " VolMax=" + DoubleToString(vol_max, 2) + " VolStep=" + DoubleToString(vol_step, 4));

    if(digits == 0 || point == 0.0 || bid == 0.0 || ask == 0.0)
    {
        Log("ReportSymbolInfoFor: INVALID symbol data for " + symbol + " — not reporting.");
        return;
    }

    string payload = StringFormat(
        "{\"symbol\":\"%s\",\"digits\":%d,\"point\":%.8f,"
        "\"volume_min\":%.2f,\"volume_max\":%.2f,\"volume_step\":%.4f,"
        "\"trade_tick_value\":%.6f,\"trade_stops_level\":%.0f,"
        "\"trade_freeze_level\":%.0f,"
        "\"filling_mode\":%d,\"visible\":%d,\"trade_mode\":%d,"
        "\"bid\":%.5f,\"ask\":%.5f}",
        symbol, (int)digits, point,
        vol_min, vol_max, vol_step,
        tick_value, stops_level, freeze_level,
        (int)filling, (int)visible, (int)trade_mode,
        bid, ask
    );

    string response;
    string url = FlaskURL + "/api/ea/report_symbol_info";
    SendPostRequest(url, payload, response);
}

void ReportCandleFor(string symbol, ENUM_TIMEFRAMES tf)
{
    if(symbol == "" || tf == 0)
        return;

    if(!EnsureSymbol(symbol))
    {
        Log("ReportCandleFor: symbol '" + symbol + "' not available");
        return;
    }

    long digits = SymbolInfoInteger(symbol, SYMBOL_DIGITS);
    double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
    if(digits == 0 || point == 0.0)
    {
        Log("ReportCandleFor: INVALID symbol data for " + symbol + " digits=" + (string)digits + " point=" + DoubleToString(point, 8));
        return;
    }

    // Always send current and previous candle — no dedup.
    // The pre-flight check needs current candle data immediately,
    // not just when a new bar appears.

    string tfName = "M15";
    if(tf == PERIOD_M1) tfName = "M1";
    else if(tf == PERIOD_M5) tfName = "M5";
    else if(tf == PERIOD_M15) tfName = "M15";
    else if(tf == PERIOD_M30) tfName = "M30";
    else if(tf == PERIOD_H1) tfName = "H1";
    else if(tf == PERIOD_H4) tfName = "H4";
    else if(tf == PERIOD_D1) tfName = "D1";

    for(int shift = 0; shift < 2; shift++)
    {
        datetime t = iTime(symbol, tf, shift);
        if(t == 0)
            continue;

        double o = iOpen(symbol, tf, shift);
        double h = iHigh(symbol, tf, shift);
        double l = iLow(symbol, tf, shift);
        double c = iClose(symbol, tf, shift);
        long   v = (long)iVolume(symbol, tf, shift);

        datetime serverTime = TimeCurrent();
        long synced = SeriesInfoInteger(symbol, tf, SERIES_SYNCHRONIZED);
        long barsCount = Bars(symbol, tf);
        Log("[TimeDebug] Symbol=" + symbol + " TF=" + tfName + " Shift=" + (string)shift
            + " MT5Time=" + TimeToString(serverTime)
            + " TimeLocal=" + TimeToString(TimeLocal())
            + " TimeTradeServer=" + TimeToString(TimeTradeServer())
            + " TimeGMT=" + TimeToString(TimeGMT())
            + " BarOpen=" + TimeToString(t)
            + " BarClose=" + TimeToString(t + (datetime)(PeriodSeconds(tf)))
            + " SecondsToClose=" + (string)((int)((t + (datetime)(PeriodSeconds(tf)) - serverTime)))
            + " SeriesSynced=" + (string)synced
            + " BarsCount=" + (string)barsCount);

        string payload = StringFormat(
            "{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"time\":%d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"tick_volume\":%d,\"shift\":%d}",
            symbol, tfName, (long)t, o, h, l, c, (int)v, shift
        );
        string response;
        string url = FlaskURL + "/api/ea/report_candle";
        SendPostRequest(url, payload, response);
    }
}
//+------------------------------------------------------------------+
