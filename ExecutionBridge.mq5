//+------------------------------------------------------------------+
//|                     ExecutionBridge.mq5 v4.0                      |
//|  - Pre-loads all trade params at arm time                         |
//|  - Executes locally with OrderSend() at candle close              |
//|  - No Sleep() calls in OnTimer (state-based cleanup)              |
//|  - Broker-reported filling mode, configurable MagicNumber/Devi    |
//|  - Break-even via server-sent be_rr/be_trigger                    |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "4.00"
#property strict

input string FlaskURL     = "http://102.203.116.146:5000";  // VPS UI API endpoint
input bool   TEST_MODE    = false;
input long   MagicNumber  = 123456;   // Configurable magic number
input int    Deviation    = 10;       // Slippage in points

enum TradeState { STATE_IDLE, STATE_ARMED, STATE_EXECUTED, STATE_CANCELLED, STATE_ERROR, STATE_DONE };
TradeState currentState = STATE_IDLE;

string pendingTradeId = "";
string pendingDirection = "";
string pendingSymbol = "";
datetime candleCloseTime = 0;
string armedSymbol = "";
int armedTfMinutes = 15;
string ea_requested_symbol = "";
datetime lastEaRequestedSymbolTime = 0;
string trackedTradeId = "";
datetime armedTime = 0;
datetime armedBarTime = 0;

double pendingLot = 0;
double pendingSl = 0;
double pendingTp = 0;
double pendingEntry = 0;
double pendingBeRr = 0;
double pendingBeTrigger = 0;
bool breakEvenApplied = false;

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

bool SendPostRequest(string url, string jsonPayload, string &response);
bool SendGetRequest(string url, string &response);
string ExtractJsonValue(string json, string key);
string Trim(string value);
void ExecuteTradeLocal();
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

void _cleanupTrade()
{
   pendingTradeId     = "";
   pendingDirection   = "";
   pendingSymbol      = "";
   pendingLot         = 0;
   pendingSl          = 0;
   pendingTp          = 0;
   pendingEntry       = 0;
   pendingBeRr        = 0;
   pendingBeTrigger   = 0;
   breakEvenApplied   = false;
   trackedTradeId     = "";
   candleCloseTime    = 0;
   armedTime          = 0;
   armedBarTime       = 0;
   armedSymbol        = "";
   armedTfMinutes     = 15;
   Comment("");
}

int OnInit()
{
   Print("Execution Bridge Started v4 - Local Execute, No Sleep, Broker Fill");
   EventSetTimer(1);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("Execution Bridge Stopped");
}

void OnTimer()
{
   ReportMarket();

   // Report symbol info for all relevant symbols every 2 seconds
   // (was 10s — too slow for the trader changing symbols)
   static datetime lastSymbolInfoReport = 0;
   if(TimeCurrent() - lastSymbolInfoReport >= 2)
   {
      ReportSymbolInfo();

      if(armedSymbol != "" && armedSymbol != _Symbol)
      {
         ReportSymbolInfoFor(armedSymbol);
         ReportCandleFor(armedSymbol, _PeriodToTf(armedTfMinutes));
         ReportTickFor(armedSymbol);
      }

      string reqSym = ea_requested_symbol;
      if(reqSym != "" && reqSym != _Symbol && reqSym != armedSymbol)
      {
         if(SymbolSelect(reqSym, true))
         {
            ReportSymbolInfoFor(reqSym);
            ReportCandleFor(reqSym, _PeriodToTf(armedTfMinutes));
            ReportTickFor(reqSym);
         }
      }
      lastSymbolInfoReport = TimeCurrent();
   }

   static datetime lastAccountReport = 0;
   if(TimeCurrent() - lastAccountReport >= 30)
   {
      ReportAccount();
      lastAccountReport = TimeCurrent();
   }

    // After execution / cancellation / error: report position + account, then clean up
    if(currentState != STATE_IDLE && currentState != STATE_ARMED)
    {
       ReportPosition();
       ReportAccount();

       // Break-even check (only after successful execution)
       if(currentState == STATE_EXECUTED && pendingSymbol != "" && pendingBeRr > 0 && pendingBeTrigger > 0)
       {
          if(!breakEvenApplied)
          {
             if(PositionSelect(pendingSymbol))
             {
                double curSl  = PositionGetDouble(POSITION_SL);
                double curTp  = PositionGetDouble(POSITION_TP);
                long   beDigits = (long)SymbolInfoInteger(pendingSymbol, SYMBOL_DIGITS);
                double triggerPrice = pendingBeTrigger;
                double currentPrice = (pendingDirection == "BUY")
                   ? SymbolInfoDouble(pendingSymbol, SYMBOL_BID)
                   : SymbolInfoDouble(pendingSymbol, SYMBOL_ASK);

                bool reached = false;
                if(pendingDirection == "BUY" && currentPrice >= triggerPrice)
                   reached = true;
                if(pendingDirection == "SELL" && currentPrice <= triggerPrice)
                   reached = true;

                if(reached && curSl != pendingEntry && curSl != 0)
                {
                   double newSl = NormalizeDouble(pendingEntry, (int)beDigits);
                   double newTp = NormalizeDouble(curTp, (int)beDigits);
                   MqlTradeRequest modReq = {};
                   MqlTradeResult modRes = {};
                   modReq.action = TRADE_ACTION_SLTP;
                   modReq.symbol = pendingSymbol;
                   modReq.sl = newSl;
                   modReq.tp = newTp;
                   if(!OrderSend(modReq, modRes))
                      Print("Break-even: OrderSend SLTP failed: ", GetLastError());
                   else
                   {
                      breakEvenApplied = true;
                      Print("Break-even applied at entry price=", newSl);
                      Comment("BREAK EVEN\nSL moved to entry");
                   }
                }
             }
          }
       }

       // After execution, check if position is still open
       if(currentState == STATE_EXECUTED)
       {
          if(!PositionSelect(pendingSymbol))
          {
             currentState = STATE_DONE;
          }
       }

       // Transition error / cancelled states to cleanup
       if(currentState == STATE_ERROR || currentState == STATE_CANCELLED)
       {
          currentState = STATE_DONE;
       }

       if(currentState == STATE_DONE)
       {
          _cleanupTrade();
          currentState = STATE_IDLE;
       }
       return;
    }

   string eaSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);
   string url = FlaskURL + "/api/ea/pending?symbol=" + eaSymbol + "&trade_id=" + UrlEncode(trackedTradeId);
   string response;
   bool ok = SendGetRequest(url, response);

   if(!ok)
   {
      Print("ERROR: Failed to poll Flask /api/ea/pending.");
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
   {
      ea_requested_symbol = requestedSymbol;
   }

   string targetSymbol = Trim(ExtractJsonValue(response, "target_symbol"));
   string tradeSymbol  = Trim(ExtractJsonValue(response, "symbol"));

   if(tradeId == "" || status == "")
      return;

   if(targetSymbol != "" && targetSymbol != _Symbol)
   {
      if(!SymbolSelect(targetSymbol, true))
         Print("WARNING: Failed to select ", targetSymbol, " in Market Watch");
      else
         ReportSymbolInfoFor(targetSymbol);
   }

   if(tradeSymbol != "" && tradeSymbol != _Symbol && tradeSymbol != targetSymbol)
   {
      if(SymbolSelect(tradeSymbol, true))
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
      pendingBeRr   = StringToDouble(ExtractJsonValue(response, "be_rr"));
      pendingBeTrigger = StringToDouble(ExtractJsonValue(response, "be_trigger"));
      breakEvenApplied = false;
      pendingSymbol = (targetSymbol != "" ? targetSymbol : tradeSymbol);

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
      PrintFormat("ARMED tf=%s now=%d close=%d wait=%d sec candle=%s tradeId=%s lot=%.2f SL=%.5f TP=%.5f beRr=%.1f beTrigger=%.1f",
                  timeframe, now, candleCloseTime, (int)(candleCloseTime - now), candleTimeStr, tradeId,
                  pendingLot, pendingSl, pendingTp, pendingBeRr, pendingBeTrigger);

      currentState  = STATE_ARMED;
      armedTime     = TimeCurrent();
      armedSymbol   = pendingSymbol;
      armedTfMinutes = tf_minutes;
      armedBarTime  = iTime(armedSymbol, _PeriodToTf(tf_minutes), 0);

      if(armedSymbol != "" && armedSymbol != _Symbol)
         SymbolSelect(armedSymbol, true);

      Print("======================================");
      Print("TRADE ARMED — all params pre-loaded");
      Print("Direction: ", direction);
      Print("Trade ID: ", tradeId);
      Print("Lot: ", pendingLot, " SL: ", pendingSl, " TP: ", pendingTp);
      Print("BE RR: ", pendingBeRr, " BE Trigger: ", pendingBeTrigger);
      Print("Waiting for candle close...");
      Print("======================================");
   }

   if(currentState == STATE_ARMED && candleCloseTime > 0)
   {
      datetime now = TimeCurrent();
      datetime currentBarTime = iTime(armedSymbol, _PeriodToTf(armedTfMinutes), 0);
      bool barChanged  = (currentBarTime != armedBarTime);
      bool timeReached = (now >= candleCloseTime);

      PrintFormat("CHECK tradeId=%s state=%d barChanged=%s timeReached=%s now=%d close=%d",
                  trackedTradeId, currentState, barChanged ? "Y" : "N", timeReached ? "Y" : "N", now, candleCloseTime);

      if(barChanged || timeReached)
      {
         Print("EXECUTE: candle closed — executing OrderSend locally");
         ExecuteTradeLocal();
      }
      else
      {
         int remaining = (int)(candleCloseTime - now);
         Comment("ARMED ", pendingDirection, " ", armedSymbol, "\n",
                 "Closes in: ", remaining, " seconds\n",
                 "Lot=", pendingLot, " SL=", pendingSl, " TP=", pendingTp);
      }
    }
}

void ExecuteTradeLocal()
{
   if(pendingTradeId == "" || pendingSymbol == "")
      return;

   pendingTradeId = Trim(pendingTradeId);

   string execSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);

   if(!SymbolSelect(execSymbol, true))
   {
      Print("ExecuteTradeLocal: Failed to select symbol ", execSymbol);
      ReportExecutionDetailed(pendingTradeId, "error", 1, "Failed to select symbol in Market Watch", 0, 0, 0, 0, 0);
      currentState = STATE_ERROR;
      Comment("FAILED\nSelect symbol in Market Watch");
      currentState = STATE_DONE;
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(execSymbol, tick) || tick.bid <= 0 || tick.ask <= 0)
   {
      Print("ExecuteTradeLocal: No tick data for ", execSymbol);
      ReportExecutionDetailed(pendingTradeId, "error", 1, "No market data for symbol", 0, 0, 0, 0, 0);
      currentState = STATE_ERROR;
      Comment("FAILED\nNo market data");
      currentState = STATE_DONE;
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

   double lot = pendingLot;
   if(lotStep > 0)
      lot = MathFloor(lot / lotStep) * lotStep;
   lot = MathMax(lot, lotMin);
   lot = MathMin(lot, lotMax);

   double reqSl = pendingSl;
   double reqTp = pendingTp;

   double minDist = MathMax(slLevel, point * 10);
   double currentPrice = (pendingDirection == "BUY") ? tick.ask : tick.bid;
   if(pendingDirection == "BUY")
   {
      if(reqSl > currentPrice - minDist)
         reqSl = NormalizeDouble(currentPrice - minDist, (int)digits);
   }
   else
   {
      if(reqTp < currentPrice + minDist)
         reqTp = NormalizeDouble(currentPrice + minDist, (int)digits);
   }

   double execEntry = (pendingDirection == "BUY") ? tick.ask : tick.bid;
   double slippage = MathAbs(execEntry - pendingEntry);
   double spread = tick.ask - tick.bid;

   request.action      = TRADE_ACTION_DEAL;
   request.symbol      = execSymbol;
   request.volume      = lot;
   request.sl          = reqSl;
   request.tp          = reqTp;
   request.deviation   = Deviation;
   request.magic       = MagicNumber;
   request.comment     = "ExecutionBot " + execSymbol + " " + pendingDirection;
   request.type_time   = ORDER_TIME_GTC;
   request.type_filling = (ENUM_ORDER_TYPE_FILLING)filling;

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
   PrintFormat("ExecuteTradeLocal: OrderSend %s %s lot=%.2f SL=%.5f TP=%.5f dev=%d magic=%d fill=%d",
               execSymbol, pendingDirection, lot, reqSl, reqTp, Deviation, (int)MagicNumber, (int)filling);

   datetime execTime = TimeCurrent();

   if(!OrderSend(request, result))
   {
      int err = GetLastError();
      Print("ExecuteTradeLocal: OrderSend returned false, error=", err);
      ReportExecutionDetailed(pendingTradeId, "error", err, "OrderSend failed", 0, 0, execEntry, slippage, spread);
      currentState = STATE_ERROR;
      Comment("FAILED\nOrderSend failed err=", err);
      currentState = STATE_DONE;
      return;
   }

   Print("ExecuteTradeLocal: retcode=", result.retcode, " comment=", result.comment,
         " order=", result.order, " deal=", result.deal);

   if(result.retcode == TRADE_RETCODE_DONE)
   {
      ReportExecutionDetailed(pendingTradeId, "executed", (int)result.retcode, "OK",
                              (long)result.order, (long)result.deal, execEntry, slippage, spread);
      currentState = STATE_EXECUTED;
      Comment("EXECUTED\n", commentStr, "\nEntry=", DoubleToString(execEntry, (int)digits),
              "\nSlippage=", DoubleToString(slippage, (int)digits));

      if(!PositionSelect(execSymbol))
      {
         Print("WARNING: No position found after execution");
      }
      else
      {
         if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
            pendingDirection = "BUY";
         else
            pendingDirection = "SELL";
      }
   }
   else
   {
      ReportExecutionDetailed(pendingTradeId, "error", (int)result.retcode, result.comment,
                              (long)result.order, (long)result.deal, execEntry, slippage, spread);
      currentState = STATE_ERROR;
      Comment("FAILED\n", result.comment);
   }
}

bool SendPostRequest(string url, string jsonPayload, string &response)
{
    Print("POST: ", url);

    uchar postData[];
    int postSize = StringToCharArray(jsonPayload, postData);
    if(postSize > 0)
       ArrayResize(postData, postSize - 1);

   uchar result[];
   string headers = "Content-Type: application/json\r\n";
   string headers_out;

   ResetLastError();
   int res = WebRequest(
       "POST",
       url,
       headers,
       10000,
       postData,
       result,
       headers_out
   );

   int err = GetLastError();

   int len = 0;
   for(int i = 0; i < ArraySize(result); i++)
   {
      if(result[i] == 0) break;
      len++;
   }
   response = CharArrayToString(result, 0, len);

   if(res >= 200 && res < 300)
   {
      return true;
   }

   Print("==============================");
   Print("URL        : ", url);
   Print("HTTP Result: ", res);
   Print("MT5 Error  : ", err);
   Print("Response   : ", response);
   Print("==============================");

   return false;
}

bool SendGetRequest(string url, string &response)
{
    Print("GET: ", url);

    uchar empty_data[];
    uchar result[];
    string headers = "";
    string headers_out;

    ResetLastError();
    int res = WebRequest(
       "GET",
       url,
       headers,
       10000,
       empty_data,
       result,
       headers_out
    );

    int err = GetLastError();

    int len = 0;
    for(int i = 0; i < ArraySize(result); i++)
    {
       if(result[i] == 0) break;
       len++;
    }
    response = CharArrayToString(result, 0, len);

    if(res >= 200 && res < 300)
    {
       return true;
    }

    Print("==============================");
    Print("URL        : ", url);
    Print("HTTP Result: ", res);
    Print("MT5 Error  : ", err);
    Print("Response   : ", response);
    Print("==============================");

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

   string execSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);
   long digits = (long)SymbolInfoInteger(execSymbol, SYMBOL_DIGITS);

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
   if(PositionSelect(posSymbol))
   {
      double volume = PositionGetDouble(POSITION_VOLUME);
      double price = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      double profit = PositionGetDouble(POSITION_PROFIT);
      string symbol = PositionGetString(POSITION_SYMBOL);
      long type = PositionGetInteger(POSITION_TYPE);
      string direction = (type == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      long ticket = PositionGetInteger(POSITION_TICKET);

      string payload = StringFormat(
         "{\"ticket\":%d,\"symbol\":\"%s\",\"direction\":\"%s\",\"lot\":%.2f,\"entry\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"profit\":%.2f}",
         (int)ticket, symbol, direction, volume, price, sl, tp, profit
      );

      string response;
      string url = FlaskURL + "/api/ea/report_position";
      SendPostRequest(url, payload, response);
   }
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
   string current = (armedSymbol != "" ? armedSymbol : _Symbol);
   MqlTick tick;
   if(!SymbolInfoTick(current, tick) || tick.bid <= 0 || tick.ask <= 0)
   {
      if(current != _Symbol && !SymbolInfoTick(_Symbol, tick))
         return;
      current = _Symbol;
   }

   string payload = StringFormat(
      "{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f}",
      current, tick.bid, tick.ask
   );
   string response;
   string url = FlaskURL + "/api/ea/report_market";
   SendPostRequest(url, payload, response);

   if(armedSymbol != "" && armedSymbol != _Symbol && armedSymbol != current)
   {
      MqlTick tick2;
      if(SymbolInfoTick(armedSymbol, tick2) && tick2.bid > 0 && tick2.ask > 0)
      {
         string payload2 = StringFormat(
            "{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f}",
            armedSymbol, tick2.bid, tick2.ask
         );
         SendPostRequest(url, payload2, response);
      }
   }

   string reqSym = ea_requested_symbol;
   if(reqSym != "" && reqSym != _Symbol && reqSym != current && reqSym != armedSymbol)
   {
      MqlTick tick3;
      if(SymbolInfoTick(reqSym, tick3) && tick3.bid > 0 && tick3.ask > 0)
      {
         string payload3 = StringFormat(
            "{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f}",
            reqSym, tick3.bid, tick3.ask
         );
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
   if(symbol == "" || !SymbolSelect(symbol, false))
   {
      Print("ReportSymbolInfoFor: symbol '", symbol, "' not available");
      return;
   }

   long   digits       = SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double point        = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double vol_min      = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vol_max      = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double vol_step     = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double tick_value   = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double stops_level  = (double)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long   filling      = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   long   visible      = SymbolInfoInteger(symbol, SYMBOL_VISIBLE);
   long   trade_mode   = SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);

   string payload = StringFormat(
      "{\"symbol\":\"%s\",\"digits\":%d,\"point\":%.8f,"
      "\"volume_min\":%.2f,\"volume_max\":%.2f,\"volume_step\":%.4f,"
      "\"trade_tick_value\":%.6f,\"trade_stops_level\":%.0f,"
      "\"filling_mode\":%d,\"visible\":%d,\"trade_mode\":%d,"
      "\"bid\":%.5f,\"ask\":%.5f}",
      symbol, (int)digits, point,
      vol_min, vol_max, vol_step,
      tick_value, stops_level,
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

   datetime t = iTime(symbol, tf, 0);
   if(t == 0)
      return;

   double o = iOpen(symbol, tf, 0);
   double h = iHigh(symbol, tf, 0);
   double l = iLow(symbol, tf, 0);
   double c = iClose(symbol, tf, 0);
   long   v = (long)iVolume(symbol, tf, 0);

   string tfName = "M15";
   if(tf == PERIOD_M1) tfName = "M1";
   else if(tf == PERIOD_M5) tfName = "M5";
   else if(tf == PERIOD_M15) tfName = "M15";
   else if(tf == PERIOD_M30) tfName = "M30";
   else if(tf == PERIOD_H1) tfName = "H1";
   else if(tf == PERIOD_H4) tfName = "H4";
   else if(tf == PERIOD_D1) tfName = "D1";

   string payload = StringFormat(
      "{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"time\":%d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"tick_volume\":%d}",
      symbol, tfName, (long)t, o, h, l, c, (int)v
   );
   string response;
   string url = FlaskURL + "/api/ea/report_candle";
   SendPostRequest(url, payload, response);
}
//+------------------------------------------------------------------+
