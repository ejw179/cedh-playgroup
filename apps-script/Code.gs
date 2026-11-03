/**
 * cEDH Playgroup: pod results endpoint.
 *
 * The website's "Report result" form POSTs here; submissions are appended to
 * the "results" tab of the Google Sheet this script is attached to. Each saved
 * submission then starts the repo's import workflow right away (if the Script
 * Property GITHUB_TOKEN is set), which records it and rebuilds the site; the
 * workflow's own schedule is the fallback.
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
    var id;
    const lock = LockService.getScriptLock();
    lock.waitLock(10000);
    try {
      id = Utilities.getUuid();
      sheet_().appendRow([id, new Date().toISOString(), JSON.stringify(body)]);
    } finally {
      lock.releaseLock();
    }
    return json_({ ok: true, id: id, started: startImport_() });
  } catch (err) {
    return json_({ ok: false, error: 'Could not read the submission: ' + err });
  }
}

/**
 * Ask GitHub to run the import workflow now. Needs Script Properties
 * GITHUB_TOKEN (a fine-grained token for this repo with Actions: Read and write)
 * and optionally GITHUB_REPO (default ejw179/cedh-playgroup). A failure here
 * never loses the submission: it's already saved and the schedule picks it up.
 */
function startImport_() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('GITHUB_TOKEN');
  if (!token) return false;
  const repo = props.getProperty('GITHUB_REPO') || 'ejw179/cedh-playgroup';
  try {
    const res = UrlFetchApp.fetch('https://api.github.com/repos/' + repo + '/actions/workflows/import.yml/dispatches', {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main' }),
      muteHttpExceptions: true,
    });
    return res.getResponseCode() === 204;
  } catch (err) {
    return false;
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
