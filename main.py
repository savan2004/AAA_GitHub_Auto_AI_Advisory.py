/****************************************************
 *  TELEGRAM MARKET SNAPSHOT FROM GOOGLE SHEETS
 *  Spreadsheet: 1g2RCYhKRmWDmJf20w4CjDQJqPyFg01nqH8NW7d4uVrg
 ****************************************************/

// ====== CONFIG – EDIT THESE TWO ONLY ======
const BOT_TOKEN = '8461087780:AAE4l58egcDN7LRbqXAp7x7x0nkfX6jTGEc';
const CHAT_ID   = '6284854709';   // user or group id

// ====== SHEET & SPREADSHEET SETTINGS ======
const SPREADSHEET_ID = '1g2RCYhKRmWDmJf20w4CjDQJqPyFg01nqH8NW7d4uVrg';
const SHEET_DASHBOARD = 'DASHBOARD';
const SHEET_GAINERS   = 'GAINERS';
const SHEET_DECLINERS = 'DECLINERS';
const SHEET_ACTIVES   = 'ACTIVES';

/*
 Assumed layout (you can adjust ranges later):

 DASHBOARD sheet:
   B4: Nifty level
   C4: Nifty change (points)
   D4: Nifty change %
   G4: Sensex level
   H4: Sensex change (points)
   I4: Sensex change %
   B7: Stocks tracked
   B8: Gainers count
   B9: Decliners count

 GAINERS / DECLINERS / ACTIVES sheet:
   Row 1 = headers
   Row 2.. = data
   A: SYMBOL  (e.g. NSE:RELIANCE)
   B: Name
   C: Change
   D: Change %
   G: Price
   H: Volume
   (ACTIVES also I: Change %)
*/

/**
 * MAIN FUNCTION – call this or attach a time trigger.
 */
function sendDailyMarketSnapshot() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);

  // ---------- 1. DASHBOARD ----------
  const dash = ss.getSheetByName(SHEET_DASHBOARD);
  if (!dash) {
    Logger.log('DASHBOARD sheet not found');
    return;
  }

  // get Nifty/Sensex + breadth
  const niftyVals  = dash.getRange('B4:D4').getValues()[0]; // [level, chg, chg%]
  const sensexVals = dash.getRange('G4:I4').getValues()[0]; // [level, chg, chg%]
  const stocksTracked = dash.getRange('B7').getValue();
  const gainersCount  = dash.getRange('B8').getValue();
  const declinersCount= dash.getRange('B9').getValue();

  const niftyLine  = 'NIFTY 50: ' + niftyVals[0] +
                     '  (' + niftyVals[1] + ' / ' + niftyVals[2] + ')';
  const sensexLine = 'SENSEX: ' + sensexVals[0] +
                     '  (' + sensexVals[1] + ' / ' + sensexVals[2] + ')';

  // ---------- 2. TOP GAINERS ----------
  const shG = ss.getSheetByName(SHEET_GAINERS);
  let gainersText = '';
  if (shG) {
    // 5 rows, 8 cols from row 2 col 1 (A2:H6)
    const gains = shG.getRange(2, 1, 5, 8).getValues();
    gains.forEach(r => {
      const sym = r[0];
      if (!sym) return;
      const name  = r[1];
      const chg   = r[2];
      const chgP  = r[3];
      const price = r[6];
      gainersText += '• ' + sym + ' (' + name + ') – ₹' + price +
                     '  [' + chg + ' / ' + chgP + ']\n';
    });
  } else {
    gainersText = '• (GAINERS sheet missing)\n';
  }

  // ---------- 3. TOP DECLINERS ----------
  const shD = ss.getSheetByName(SHEET_DECLINERS);
  let declinersText = '';
  if (shD) {
    const decs = shD.getRange(2, 1, 5, 8).getValues();
    decs.forEach(r => {
      const sym = r[0];
      if (!sym) return;
      const name  = r[1];
      const chg   = r[2];
      const chgP  = r[3];
      const price = r[6];
      declinersText += '• ' + sym + ' (' + name + ') – ₹' + price +
                       '  [' + chg + ' / ' + chgP + ']\n';
    });
  } else {
    declinersText = '• (DECLINERS sheet missing)\n';
  }

  // ---------- 4. TOP ACTIVES ----------
  const shA = ss.getSheetByName(SHEET_ACTIVES);
  let activesText = '';
  if (shA) {
    const acts = shA.getRange(2, 1, 5, 9).getValues(); // A2:I6
    acts.forEach(r => {
      const sym = r[0];
      if (!sym) return;
      const name  = r[1];
      const price = r[6];
      const vol   = r[7];
      const chgP  = r[8];
      activesText += '• ' + sym + ' (' + name + ') – ₹' + price +
                     '  Vol: ' + vol + '  [' + chgP + ']\n';
    });
  } else {
    activesText = '• (ACTIVES sheet missing)\n';
  }

  // ---------- 5. BUILD TELEGRAM MESSAGE ----------
  const now = Utilities.formatDate(
    new Date(),
    Session.getScriptTimeZone(),
    'dd-MM-yyyy HH:mm'
  );

  const msg =
    '🇮🇳 <b>STOCK MARKET SNAPSHOT</b>\n' +
    now + '\n' +
    '━━━━━━━━━━━━━━\n' +
    niftyLine + '\n' +
    sensexLine + '\n\n' +
    'Stocks tracked: ' + stocksTracked +
    ' | Gainers: ' + gainersCount +
    ' | Decliners: ' + declinersCount + '\n' +
    '━━━━━━━━━━━━━━\n' +
    '<b>TOP GAINERS</b>\n' +
    (gainersText || '• No data\n') +
    '━━━━━━━━━━━━━━\n' +
    '<b>TOP DECLINERS</b>\n' +
    (declinersText || '• No data\n') +
    '━━━━━━━━━━━━━━\n' +
    '<b>TOP ACTIVES</b>\n' +
    (activesText || '• No data\n') +
    '\nNote: Data from Google Finance dashboard, for educational use only.';

  // ---------- 6. SEND TO TELEGRAM ----------
  const url = 'https://api.telegram.org/bot' + BOT_TOKEN + '/sendMessage';
  const payload = {
    chat_id: CHAT_ID,
    text: msg,
    parse_mode: 'HTML'
  };

  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  Logger.log(res.getContentText());
}

/**
 * Utility: simple test in log only (no Telegram).
 */
function testReadSheets() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  Logger.log(ss.getSheetByName(SHEET_DASHBOARD).getRange('B4:D4').getValues());
}
