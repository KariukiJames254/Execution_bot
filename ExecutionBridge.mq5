//+------------------------------------------------------------------+
//|                     ExecutionBridge.mq5                          |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property version   "2.00"
#property strict

input string FlaskURL   = "http://127.0.0.1:5000";
input bool   TEST_MODE  = false;

enum TradeState { STATE_IDLE, STATE_ARMED, STATE_EXECUTED, STATE_CANCELLED, STATE_ERROR };
TradeState currentState = STATE_IDLE;

string pendingTradeId = "";
string pendingDirection = "";
datetime candleCloseTime = 0;
string trackedTradeId = "";

bool SendPostRequest(string url, string jsonPayload, string &response);
bool SendGetRequest(string url, string &response);
string ExtractJsonValue(string json, string key);
string Trim(string value);

int OnInit()
{
   Print("Execution Bridge Started");
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
   if(currentState != STATE_IDLE && currentState != STATE_ARMED)
      return;

   string url = FlaskURL + "/api/ea/pending?symbol=" + _Symbol;
   string response;
   bool ok = SendGetRequest(url, response);

   if(!ok)
   {
      Print("Flask not reachable");
      return;
   }

   string tradeId = Trim(ExtractJsonValue(response, "trade_id"));
   string status = Trim(ExtractJsonValue(response, "status"));

   if(tradeId == "" || status == "")
      return;

    if(trackedTradeId != tradeId && status == "armed")
    {
       string direction = Trim(ExtractJsonValue(response, "direction"));
       string candleTimeStr = Trim(ExtractJsonValue(response, "candle_time"));
       string timeframe = Trim(ExtractJsonValue(response, "timeframe"));

       trackedTradeId = tradeId;
       pendingTradeId = tradeId;
       pendingDirection = direction;

       MqlDateTime dt;
       string normalizedTime = StringReplace(candleTimeStr, "T", " ");
       datetime candleStart = StringToTime(normalizedTime);
       TimeToStruct(candleStart, dt);

       int tf_minutes = 15;
       if(timeframe == "M1") tf_minutes = 1;
       else if(timeframe == "M5") tf_minutes = 5;
       else if(timeframe == "M15") tf_minutes = 15;
       else if(timeframe == "M30") tf_minutes = 30;
       else if(timeframe == "H1") tf_minutes = 60;
       else if(timeframe == "H4") tf_minutes = 240;
       else if(timeframe == "D1") tf_minutes = 1440;

       dt.min += tf_minutes;
       if(dt.min >= 60) { dt.min -= 60; dt.hour++; }
       if(dt.hour >= 24) { dt.hour -= 24; dt.day++; }
       candleCloseTime = StructToTime(dt);

       datetime now = TimeCurrent();
       int safety = 0;
       while(candleCloseTime <= now && safety < 50)
       {
          candleCloseTime += tf_minutes * 60;
          safety++;
       }
       PrintFormat("NOW=%d CLOSE=%d SKEW=%d sec", now, candleCloseTime, (int)(candleCloseTime - now));

       currentState = STATE_ARMED;

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

      if(now >= candleCloseTime)
      {
         Print("Candle closed! Executing trade...");
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
      url = FlaskURL + "/api/execute_trade?trade_id=" + encodedId + "&test_mode=1&symbol=" + _Symbol + "&direction=" + pendingDirection;
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
      return;
   }

   Print("Execution response: ", response);

   if(StringFind(response, "\"status\":\"executed\"") >= 0)
   {
      string ticket = ExtractJsonValue(response, "ticket");
      string entry = ExtractJsonValue(response, "entry");
      string slippage = ExtractJsonValue(response, "slippage");

      Print("======================================");
      Print("TRADE EXECUTED");
      Print("Ticket: ", ticket);
      Print("Entry: ", entry);
      Print("Slippage: ", slippage, " pips");
      Print("======================================");

      currentState = STATE_EXECUTED;
      Comment("EXECUTED\nTicket: ", ticket, "\nEntry: ", entry);

      Sleep(10000);
      currentState = STATE_IDLE;
      pendingTradeId = "";
      pendingDirection = "";
      trackedTradeId = "";
      candleCloseTime = 0;
      Comment("");
   }
   else
   {
      string errorCode = ExtractJsonValue(response, "retcode");
      string errorComment = ExtractJsonValue(response, "comment");

      Print("ERROR: Execution failed. Code: ", errorCode, " Comment: ", errorComment);
      currentState = STATE_ERROR;
      Comment("FAILED\n", errorComment);
   }
}

bool SendPostRequest(string url, string jsonPayload, string &response)
{
   uchar postData[];
   StringToCharArray(jsonPayload, postData);

   uchar result[];
   string headers = "Content-Type: application/json\r\n";
   string headers_out;

   int res = WebRequest(
      "POST",
      url,
      headers,
      10000,
      postData,
      result,
      headers_out
    );

    if(ArraySize(result) > 0)
    {
       ArrayResize(result, ArraySize(result) + 1);
       result[ArraySize(result) - 1] = 0;
    }
    response = CharArrayToString(result);

    if(res == 200)
    {
       return true;
    }

    Print("WebRequest failed. Code: ", res, " Response: ", response);
    if(res == -1)
       Print("Check MT5 Options -> Expert Advisors -> Allow WebRequest");

    return false;
}

bool SendGetRequest(string url, string &response)
{
    uchar empty_data[];
    uchar result[];
    string headers = "";
    string headers_out;

    int res = WebRequest(
       "GET",
       url,
       headers,
       10000,
       empty_data,
       result,
       headers_out
    );

    if(ArraySize(result) > 0)
    {
       ArrayResize(result, ArraySize(result) + 1);
       result[ArraySize(result) - 1] = 0;
    }
    response = CharArrayToString(result);

    if(res == 200)
    {
       return true;
    }

    Print("WebRequest GET failed. Code: ", res, " Response: ", response);
    if(res == -1)
       Print("Check MT5 Options -> Expert Advisors -> Allow WebRequest");

    return false;
}

string UrlEncode(string value)
{
   value = StringReplace(value, ":", "%3A");
   value = StringReplace(value, " ", "%20");
   value = StringReplace(value, "+", "%2B");
   value = StringReplace(value, "#", "%23");
   value = StringReplace(value, "%", "%25");
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
         (json[pos] >= '0' && json[pos] <= '9' ||
          json[pos] == '.' || json[pos] == '-' || json[pos] == 'e' || json[pos] == 'E'))
   {
      num += json[pos];
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
