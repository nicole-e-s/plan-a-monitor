// Re-tag endpoint for the Plan A monitor mention log.
//
// Where this runs: inside a Google Sheet, as an Apps Script web app. It is
// the tiny "server" that receives one-click tag corrections from
// mentions.html and appends them to the sheet; the monitor then reads the
// sheet's published CSV and applies them (see apply_retags in planamonitor.py).
//
// Setup (once):
//   1. Create a Google Sheet -> Extensions -> Apps Script -> paste this file.
//   2. Change SECRET below to the team password (share it with comms).
//   3. Deploy -> New deployment -> Web app -> Execute as: Me,
//      Who has access: Anyone -> copy the /exec URL into config.cloud.yaml
//      as retag.post_url.
//   4. Back in the sheet: File -> Share -> Publish to web -> select the
//      "retags" tab, format CSV -> copy that URL into config.cloud.yaml as
//      retag.sheet_csv_url.
//
// The script code and SECRET are never visible to page visitors; only the
// /exec URL is public, and posts without the password are rejected.

const SECRET = 'CHANGE-ME';
const SHEET_NAME = 'retags';

function doPost(e) {
  const out = ContentService.createTextOutput();
  try {
    const d = JSON.parse(e.postData.contents);
    if (!d || d.key !== SECRET) { out.setContent('bad key'); return out; }
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sh = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
    if (sh.getLastRow() === 0) {
      sh.appendRow(['Timestamp', 'Mention URL', 'Correct sentiment', 'Correct scope', 'Source']);
    }
    sh.appendRow([new Date(), String(d.url || ''), String(d.sentiment || ''),
                  String(d.scope || ''), 'dashboard']);
    out.setContent('ok');
  } catch (err) {
    out.setContent('error: ' + err);
  }
  return out;
}
