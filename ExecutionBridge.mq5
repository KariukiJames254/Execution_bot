//+------------------------------------------------------------------+
//|                     ExecutionBridge.mq5                          |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "3.00"
#property strict

input string FlaskURL   = "http://102.203.116.146:5000";  // VPS UI API endpoint
input bool   TEST_MODE  = false;

enum TradeState { STATE_IDLE, STATE_ARMED, STATE_EXECUTED, STATE_CANCELLED, STATE_ERROR };
TradeState currentState = STATE_IDLE;

string pendingTradeId = "";
string pendingDirection = "";
datetime candleCloseTime = 0;
string armedSymbol = "";
int armedTfMinutes = 15;
string trackedTradeId = "";
datetime armedTime = 0;
datetime armedBarTime = 0;

ENUM_TIMEFRAMES _PeriodToTf(int tf_minutes);
bool SendPostRequest(string url, string jsonPayload, string &response);
bool SendGetRequest(string url, string &response);
string ExtractJsonValue(string json, string key);
string Trim(string value);
void ReportMarket();
void ReportSymbolInfo();
void ReportSymbolInfoFor(string symbol);
void ReportAccount();

int OnInit()
{
   Print("Execution Bridge Started - VPS Reporting v3");
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

   static datetime lastSymbolInfoReport = 0;
   if(TimeCurrent() - lastSymbolInfoReport >= 10)
   {
      ReportSymbolInfo();
      if(armedSymbol != "" && armedSymbol != _Symbol)
         ReportSymbolInfoFor(armedSymbol);
      lastSymbolInfoReport = TimeCurrent();
   }

   static datetime lastAccountReport = 0;
   if(TimeCurrent() - lastAccountReport >= 30)
   {
      ReportAccount();
      lastAccountReport = TimeCurrent();
   }

   if(currentState != STATE_IDLE && currentState != STATE_ARMED)
   {
      ReportPosition();
      ReportAccount();
      return;
   }

    string eaSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);
    string url = FlaskURL + "/api/ea/pending?symbol=" + eaSymbol + "&trade_id=" + UrlEncode(trackedTradeId);
    string response;
    bool ok = SendGetRequest(url, response);

    if(!ok)
    {
       Print("ERROR: Failed to send execution request.");
       currentState = STATE_ERROR;
       Comment("FAILED\nConnection error");
       Sleep(5000);
       currentState = STATE_IDLE;
       pendingTradeId = "";
       pendingDirection = "";
       trackedTradeId = "";
       candleCloseTime = 0;
       armedTime = 0;
       armedBarTime = 0;
       armedSymbol = "";
       armedTfMinutes = 15;
       Comment("");
       return;
    }

    string tradeId = Trim(ExtractJsonValue(response, "trade_id"));
    string status = Trim(ExtractJsonValue(response, "status"));
    string targetSymbol = Trim(ExtractJsonValue(response, "target_symbol"));

    if(tradeId == "" || status == "")
       return;

    if(targetSymbol != "" && targetSymbol != _Symbol)
    {
       if(!SymbolSelect(targetSymbol, true))
       {
          Print("WARNING: Failed to select ", targetSymbol, " in Market Watch");
       }
       else
       {
          ReportSymbolInfoFor(targetSymbol);
       }
    }

    if(trackedTradeId != tradeId && status == "armed")
    {
       string direction = Trim(ExtractJsonValue(response, "direction"));
       string candleTimeStr = Trim(ExtractJsonValue(response, "candle_time"));
       string timeframe = Trim(ExtractJsonValue(response, "timeframe"));

       trackedTradeId = tradeId;
       pendingTradeId = tradeId;
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
                   dt.hour = ((dt.hour / tf_hours) + 1) * tf_hours;
                dt.min = 0;
             }

             if(dt.min >= 60) { dt.min -= 60; dt.hour++; }
             if(dt.hour >= 24) { dt.hour -= 24; dt.day++; }
             candleCloseTime = StructToTime(dt);
             safety++;
          }
          while(candleCloseTime <= now && safety < 50);
       }

       datetime now = TimeCurrent();
       PrintFormat("ARMED tf=%s now=%d close=%d wait=%d sec candle=%s tradeId=%s candleCloseStr=%s", timeframe, now, candleCloseTime, (int)(candleCloseTime - now), candleTimeStr, tradeId, candleCloseStr);

       currentState = STATE_ARMED;
       armedTime = TimeCurrent();
       armedSymbol = (targetSymbol != "" ? targetSymbol : _Symbol);
       armedTfMinutes = tf_minutes;
       armedBarTime = iTime(armedSymbol, _PeriodToTf(tf_minutes), 0);

       Print("======================================");
       Print("TRADE ARMED");
       Print("Direction: ", direction);
       Print("Trade ID: ", tradeId);
       Print("Candle: ", candleTimeStr);
       Print("Waiting for candle close...");
       Print("======================================");
    }

    if(currentState == STATE_ARMED && candleCloseTime > 0)
    {
       datetime now = TimeCurrent();
       datetime currentBarTime = iTime(armedSymbol, _PeriodToTf(armedTfMinutes), 0);
       bool barChanged = (currentBarTime != armedBarTime);
       bool timeReached = (now >= candleCloseTime);

      PrintFormat("CHECK tradeId=%s state=%d barChanged=%s timeReached=%s currentBarTime=%d armedBarTime=%d now=%d close=%d", trackedTradeId, currentState, barChanged ? "Y" : "N", timeReached ? "Y" : "N", currentBarTime, armedBarTime, now, candleCloseTime);

      if(barChanged || timeReached)
      {
         Print("EXECUTE: firing ExecuteTrade()");
         currentState = STATE_EXECUTED;
         ExecuteTrade();
      }
      else
      {
         int remaining = (int)(candleCloseTime - now);
         Comment("ARMED ", pendingDirection, "\n",
                 "Candle: ", ExtractJsonValue(response, "candle_time"), "\n",
                 "Closes in: ", remaining, " seconds");
      }
   }
}

void ExecuteTrade()
{
   if(pendingTradeId == "") return;

   pendingTradeId = Trim(pendingTradeId);

   string url;
    if(TEST_MODE)
    {
       string encodedId = UrlEncode(pendingTradeId);
       string execSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);
       url = FlaskURL + "/api/execute_trade?trade_id=" + encodedId + "&test_mode=1&symbol=" + execSymbol + "&direction=" + pendingDirection;
   }
   else
   {
      string encodedId = UrlEncode(pendingTradeId);
      url = FlaskURL + "/api/execute_trade?trade_id=" + encodedId;
   }
   string response;
   bool ok = SendGetRequest(url, response);

   if(!ok)
   {
       Print("ERROR: Failed to send execution request.");
       currentState = STATE_ERROR;
       Comment("FAILED\nConnection error");
       Sleep(5000);
       currentState = STATE_IDLE;
        pendingTradeId = "";
        pendingDirection = "";
        trackedTradeId = "";
        candleCloseTime = 0;
        armedTime = 0;
        armedBarTime = 0;
        armedSymbol = "";
        armedTfMinutes = 15;
        Comment("");
        return;
    }

    Print("Execution response: ", response);

    if(StringFind(response, "\"status\":\"executed\"") >= 0)
    {
       Print("EXECUTE_SUCCESS");
       ReportExecution(pendingTradeId, "executed", "0", "OK");

       currentState = STATE_EXECUTED;
       Comment("EXECUTED\nCheck dashboard for details");

        Sleep(10000);
        currentState = STATE_IDLE;
        pendingTradeId = "";
        pendingDirection = "";
        trackedTradeId = "";
        candleCloseTime = 0;
        armedTime = 0;
         armedBarTime = 0;
         armedSymbol = "";
         armedTfMinutes = 15;
         Comment("");
     }
     else if(StringFind(response, "\"status\":\"queued\"") >= 0)
    {
       Print("EXECUTE_QUEUED");
       ReportExecution(pendingTradeId, "queued", "0", "Queued");
       currentState = STATE_EXECUTED;
       Comment("QUEUED\nWaiting for broker fill");

       Sleep(5000);
       currentState = STATE_IDLE;
        pendingTradeId = "";
        pendingDirection = "";
        trackedTradeId = "";
        candleCloseTime = 0;
        armedTime = 0;
        armedBarTime = 0;
        armedSymbol = "";
        armedTfMinutes = 15;
        Comment("");
     }
     else
    {
       string errorCode = ExtractJsonValue(response, "retcode");
       string errorComment = ExtractJsonValue(response, "comment");
       if(errorCode == "" && errorComment == "")
       {
          errorCode = ExtractJsonValue(response, "error");
          errorComment = "";
       }

       Print("ERROR: Execution failed. Code: ", errorCode, " Comment: ", errorComment);
       ReportExecution(pendingTradeId, "error", errorCode, errorComment);
       currentState = STATE_ERROR;
       Comment("FAILED\n", errorComment);
       Sleep(5000);
       currentState = STATE_IDLE;
       pendingTradeId = "";
       pendingDirection = "";
       trackedTradeId = "";
       candleCloseTime = 0;
       armedTime = 0;
       armedBarTime = 0;
       Comment("");
    }
}

bool SendPostRequest(string url, string jsonPayload, string &response)
{
    Print("POST: ", url);
     
     uchar postData[];
     int postSize = StringToCharArray(jsonPayload, postData);
     // WebRequest sends the full array; omit StringToCharArray's trailing NUL
     // so Flask receives valid JSON rather than JSON followed by a NUL byte.
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

void ReportExecution(string tradeId, string status, string retcode, string comment)
{
   if(tradeId == "") return;
   
   MqlTradeRequest request = {};
   MqlTradeResult result = {};
   
    string execSymbol = (armedSymbol != "" ? armedSymbol : _Symbol);
    double bid = SymbolInfoDouble(execSymbol, SYMBOL_BID);
    double ask = SymbolInfoDouble(execSymbol, SYMBOL_ASK);
    double entry = (_Period == 0) ? ask : (pendingDirection == "BUY" ? ask : bid);
   
   string payload = StringFormat(
      "{\"trade_id\":\"%s\",\"status\":\"%s\",\"ticket\":%d,\"deal\":%d,\"entry\":%.5f,\"slippage\":%.5f,\"retcode\":%s,\"comment\":\"%s\"}",
      tradeId, status, (int)result.order, (int)result.deal, entry, 0.0, retcode, comment
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

   if(armedSymbol != "" && armedSymbol != _Symbol)
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

   string payload = StringFormat(
      "{\"symbol\":\"%s\",\"digits\":%d,\"point\":%.8f,"
      "\"volume_min\":%.2f,\"volume_max\":%.2f,\"volume_step\":%.4f,"
      "\"trade_tick_value\":%.6f,\"trade_stops_level\":%.0f,"
      "\"filling_mode\":%d,\"visible\":%d,\"trade_mode\":%d}",
      symbol, (int)digits, point,
      vol_min, vol_max, vol_step,
      tick_value, stops_level,
      (int)filling, (int)visible, (int)trade_mode
   );

   string response;
   string url = FlaskURL + "/api/ea/report_symbol_info";
   SendPostRequest(url, payload, response);
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
