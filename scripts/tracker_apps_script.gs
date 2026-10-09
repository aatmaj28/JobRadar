// Job radar tracker for Google Sheets (use your personal account, aatmajsalunke@gmail.com).
// 1) Make a new Google Sheet, then Extensions > Apps Script, delete what's there, paste this file.
// 2) Change TOKEN below to a long random string. Save the same string as the SHEET_TOKEN secret.
// 3) Pick the function "setup" and press Run once. Approve the permissions.
// 4) Deploy > New deployment > type Web app > Execute as: Me > Who has access: Anyone > Deploy.
//    Copy the web app URL into the SHEET_WEBHOOK_URL secret.

const TOKEN = 'CHANGE_ME_TO_A_LONG_RANDOM_STRING';
const SHEET_NAME = 'Applications';
const HEADERS = ['Found', 'Company', 'Role', 'Location', 'Fit', 'Why', 'Source', 'Link',
                 'Status', 'Applied on', 'Follow up on', 'Referral asked', 'Notes', 'Key'];
const STATUSES = ['New', 'Applied', 'Referral asked', 'Interview', 'Offer', 'Rejected', 'Skipped'];

function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold');
  sh.setFrozenRows(1);
  if (sh.getMaxRows() < 5001) sh.insertRowsAfter(sh.getMaxRows(), 5001 - sh.getMaxRows());
  const rule = SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).build();
  sh.getRange(2, 9, 5000, 1).setDataValidation(rule);
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('onStatusEdit').forSpreadsheet(ss).onEdit().create();
  ScriptApp.newTrigger('dailyFollowUps').timeBased().everyDays(1).atHour(8).create();
}

// The radar posts new matches here.
function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (body.token !== TOKEN) return json_({error: 'bad token'});
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  const last = sh.getLastRow();
  const keys = new Set(last > 1 ? sh.getRange(2, 14, last - 1, 1).getValues().map(r => r[0]) : []);
  const rows = (body.rows || []).filter(r => !keys.has(r.key)).map(r =>
      [new Date(), r.company, r.title, r.location, r.score, r.why, r.source, r.url, 'New', '', '', '', '', r.key]);
  if (rows.length) sh.getRange(last + 1, 1, rows.length, HEADERS.length).setValues(rows);
  return json_({added: rows.length});
}

// Set Status to Applied and it stamps today plus a follow up date one week out.
function onStatusEdit(e) {
  const sh = e.range.getSheet();
  if (sh.getName() !== SHEET_NAME || e.range.getColumn() !== 9 || e.range.getRow() < 2) return;
  const row = e.range.getRow();
  if (String(e.value) === 'Applied' && !sh.getRange(row, 10).getValue()) {
    const now = new Date();
    sh.getRange(row, 10).setValue(now);
    sh.getRange(row, 11).setValue(new Date(now.getTime() + 7 * 864e5));
  }
}

// 8am email listing applications that are due a follow up.
function dailyFollowUps() {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  const last = sh.getLastRow();
  if (last < 2) return;
  const today = new Date();
  const due = sh.getRange(2, 1, last - 1, HEADERS.length).getValues()
      .filter(r => r[8] === 'Applied' && r[10] && new Date(r[10]) <= today);
  if (!due.length) return;
  const tz = Session.getScriptTimeZone();
  const lines = due.map(r => `• ${r[2]} at ${r[1]} (applied ${Utilities.formatDate(new Date(r[9]), tz, 'MMM d')}) ${r[7]}`);
  MailApp.sendEmail(Session.getEffectiveUser().getEmail(), `Follow up on ${due.length} applications`,
      'It has been a week on these. A short note to the recruiter or your referral helps:\n\n' + lines.join('\n'));
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
