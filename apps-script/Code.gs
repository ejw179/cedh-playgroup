/**
 * cEDH Playgroup: pod results endpoint.
 *
 * The website's "Report result" form POSTs here; submissions are appended to
 * the "results" tab of the Google Sheet this script is attached to. The repo's
 * import workflow GETs them every 30 minutes and records them as games.
 *
 * Setup (once): see apps-script/README.md. In short: make a Google Sheet,
 * Extensions > Apps Script, paste this file, set a Script Property PIN, and
 * Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
 */

const TAB = 'results';
const MAX_BYTES = 20000;

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(TAB);
  if (!sh) {
    sh = ss.insertSheet(TAB);
    sh.appendRow(['id', 'received', 'payload']);
  }
  return sh;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** Save one pod result. The site sends JSON as text/plain (no CORS preflight). */
function doPost(e) {
  try {
    const raw = (e && e.postData && e.postData.contents) || '';
    if (raw.length > MAX_BYTES) return json_({ ok: false, error: 'Submission too long.' });
    const body = JSON.parse(raw);
    const pin = PropertiesService.getScriptProperties().getProperty('PIN');
    if (!pin) return json_({ ok: false, error: 'Reporting is not set up (no PIN configured).' });
    if (String(body.pin || '').trim() !== pin) return json_({ ok: false, error: 'Wrong group PIN.' });
    delete body.pin;
    const lock = LockService.getScriptLock();
    lock.waitLock(10000);
    try {
      const id = Utilities.getUuid();
      sheet_().appendRow([id, new Date().toISOString(), JSON.stringify(body)]);
      return json_({ ok: true, id: id });
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return json_({ ok: false, error: 'Could not read the submission: ' + err });
  }
}

/** List every submission (they're published on the site anyway, so no PIN). */
function doGet() {
  const rows = sheet_().getDataRange().getValues().slice(1);
  const results = [];
  rows.forEach(function (r) {
    try {
      results.push({ id: String(r[0]), received: String(r[1]), payload: JSON.parse(r[2]) });
    } catch (err) {
      // skip a row someone edited by hand into invalid JSON
    }
  });
  return json_({ ok: true, results: results });
}
