/**
 * SlideEgg WhatsApp Channel — Sheet + Email reports
 * ------------------------------------------------------------------
 * Runs inside your own Google account, so:
 *   - email is sent FROM your official address (no App Password needed)
 *   - the Google Sheet is created and updated automatically
 *   - nothing is paid for, and no third-party service is involved
 *
 * SETUP (once, about 5 minutes)
 *   1. Go to  script.google.com  and sign in as mukunthini@slideegg.com
 *   2. New project  ->  delete whatever code is there  ->  paste ALL of this
 *   3. Save (the disk icon). Name it "SlideEgg WhatsApp Reports".
 *   4. In the function dropdown at the top pick  setup  and press Run.
 *   5. Google will ask for permission — Review permissions -> choose your
 *      account -> Advanced -> "Go to ... (unsafe)" -> Allow.
 *      ("unsafe" only means the script is not published on the Google
 *      marketplace. It is your own code, running in your own account.)
 *   6. Done. The Execution log prints the link to your new Sheet.
 *
 * After that it runs on its own:
 *   - every day at 21:00  -> sheet updated + daily email
 *   - every Monday 09:00  -> weekly email
 */

// ------------------------------------------------------------------ settings

var REPO       = 'mukunthini-svg/slideegg-whatsapp-bot';
var MAIL_TO    = 'admin@slideegg.com';
var SHEET_NAME = 'SlideEgg WhatsApp — Posted Log';
var TIMEZONE   = 'Asia/Kolkata';
var BRAND      = '#1F5C8B';

// ------------------------------------------------------------------ setup

/** Run this once. Creates the sheet and the two schedules. */
function setup() {
  var ss = getSheet_().getParent();

  // remove any triggers from a previous setup so this is safe to re-run
  ScriptApp.getProjectTriggers().forEach(function (t) {
    ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('dailyJob').timeBased()
    .atHour(21).nearMinute(0).everyDays(1).inTimezone(TIMEZONE).create();

  ScriptApp.newTrigger('weeklyJob').timeBased()
    .onWeekDay(ScriptApp.WeekDay.MONDAY)
    .atHour(9).nearMinute(0).inTimezone(TIMEZONE).create();

  syncSheet();

  Logger.log('Setup complete.');
  Logger.log('Your sheet: ' + ss.getUrl());
  Logger.log('Daily email 21:00, weekly email Monday 09:00, to ' + MAIL_TO);
  Logger.log('Add that sheet link as the SHEET_URL secret on GitHub if you '
           + 'want it to appear in the GitHub-side emails too.');
  return ss.getUrl();
}

/** Send yourself a test email right now, without waiting for 21:00. */
function testDailyNow() {
  syncSheet();
  sendReport_(false);
}

function testWeeklyNow() {
  syncSheet();
  sendReport_(true);
}

function dailyJob()  { syncSheet(); sendReport_(false); }
function weeklyJob() { syncSheet(); sendReport_(true); }

// ------------------------------------------------------------------ data

function fetchText_(path) {
  // cache-buster: raw.githubusercontent caches hard, and a stale copy here
  // would mean reporting yesterday's numbers as if they were today's
  var url = 'https://raw.githubusercontent.com/' + REPO + '/refs/heads/main/'
          + path + '?nc=' + new Date().getTime();
  var res = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    headers: { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' }
  });
  if (res.getResponseCode() !== 200) {
    Logger.log('fetch ' + path + ' -> HTTP ' + res.getResponseCode());
    return null;
  }
  return res.getContentText();
}

function fetchJson_(path) {
  var t = fetchText_(path);
  if (!t) return {};
  try { return JSON.parse(t); } catch (e) { return {}; }
}

/** Minimal RFC-4180 CSV parser: handles quotes, commas and newlines in fields. */
function parseCsv_(text) {
  if (!text) return [];
  var rows = [], row = [], field = '', inQuotes = false;
  for (var i = 0; i < text.length; i++) {
    var c = text.charAt(i);
    if (inQuotes) {
      if (c === '"') {
        if (text.charAt(i + 1) === '"') { field += '"'; i++; }
        else { inQuotes = false; }
      } else { field += c; }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(field); field = '';
    } else if (c === '\n') {
      row.push(field); rows.push(row); row = []; field = '';
    } else if (c !== '\r') {
      field += c;
    }
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter(function (r) { return r.length > 1 || r[0] !== ''; });
}

function loadPosts_() {
  var rows = parseCsv_(fetchText_('state/posts.csv'));
  if (rows.length < 2) return { header: [], data: [] };
  return { header: rows[0], data: rows.slice(1) };
}

// ------------------------------------------------------------------ sheet

function getSheet_() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('SHEET_ID');
  var ss = null;
  if (id) {
    try { ss = SpreadsheetApp.openById(id); } catch (e) { ss = null; }
  }
  if (!ss) {
    ss = SpreadsheetApp.create(SHEET_NAME);
    props.setProperty('SHEET_ID', ss.getId());
    Logger.log('Created a new sheet: ' + ss.getUrl());
  }
  return ss.getSheets()[0];
}

/** Mirror posts.csv into the sheet, newest first. */
function syncSheet() {
  var sheet = getSheet_();
  var posts = loadPosts_();
  if (!posts.header.length) {
    Logger.log('posts.csv is empty or unreadable — the sheet was left alone.');
    return;
  }

  var data = posts.data.slice().sort(function (a, b) {
    var k1 = (a[0] || '') + ' ' + (a[1] || '');
    var k2 = (b[0] || '') + ' ' + (b[1] || '');
    return k2 < k1 ? -1 : (k2 > k1 ? 1 : 0);   // newest first
  });

  sheet.clear();
  sheet.getRange(1, 1, 1, posts.header.length).setValues([posts.header]);
  if (data.length) {
    // pad every row to the header width, or setValues throws
    var w = posts.header.length;
    var padded = data.map(function (r) {
      var out = r.slice(0, w);
      while (out.length < w) out.push('');
      return out;
    });
    sheet.getRange(2, 1, padded.length, w).setValues(padded);
  }

  sheet.getRange(1, 1, 1, posts.header.length)
       .setFontWeight('bold').setBackground(BRAND).setFontColor('#ffffff');
  sheet.setFrozenRows(1);
  for (var c = 1; c <= posts.header.length; c++) sheet.autoResizeColumn(c);
  sheet.getParent().rename(SHEET_NAME);

  Logger.log('Sheet updated: ' + data.length + ' rows.');
}

// ------------------------------------------------------------------ health

/** Mirrors the health logic on the GitHub side. Returns {problem, headline, detail}. */
function healthNote_() {
  var run = fetchJson_('state/last_run.json');
  var health = fetchJson_('state/health.json');
  var now = new Date();

  if (!run || !run.run) {
    return { problem: true, headline: 'No run record found',
             detail: 'state/last_run.json is missing. The bot may not be running at all.' };
  }
  if (run.failed) {
    return { problem: true, headline: run.failed + ' post(s) failed to send',
             detail: (run.failed_urls || []).join(', ') || 'See the run log on GitHub.' };
  }
  if (run.mode === 'dry') {
    return { problem: true, headline: 'The bot is in preview mode — nothing is being sent',
             detail: 'Reason: ' + (run.why_dry || 'unknown') };
  }

  var quiet = hoursSince_(health.last_new_item_at, now);
  if (quiet !== null && quiet > 24) {
    return { problem: true,
             headline: 'Nothing new detected for ' + Math.round(quiet) + ' hours',
             detail: 'Compare diagnostics.page1_top3 in the latest run with the live '
                   + 'site — the runner may be receiving a stale cached page.' };
  }

  var gap = hoursSince_(run.run, now);
  if (gap !== null && gap > 6) {
    return { problem: true,
             headline: 'The bot has not run for ' + Math.round(gap) + ' hours',
             detail: 'Check the Actions tab on GitHub — the schedule may be disabled.' };
  }
  return { problem: false, headline: 'All healthy', detail: '' };
}

function hoursSince_(iso, now) {
  if (!iso) return null;
  var t = new Date(iso);
  if (isNaN(t.getTime())) return null;
  return (now.getTime() - t.getTime()) / 3600000;
}

// ------------------------------------------------------------------ email

function fmt_(d) { return Utilities.formatDate(d, TIMEZONE, 'yyyy-MM-dd'); }

function esc_(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function sendReport_(weekly) {
  var posts = loadPosts_();
  var idx = {};
  posts.header.forEach(function (h, i) { idx[h] = i; });

  var now = new Date();
  var endStr = fmt_(now);
  var start = new Date(now.getTime() - (weekly ? 6 : 0) * 86400000);
  var startStr = fmt_(start);

  var rows = posts.data.filter(function (r) {
    var d = r[idx['date']];
    return d >= startStr && d <= endStr;
  }).sort(function (a, b) {
    var k1 = a[idx['date']] + ' ' + a[idx['time_ist']];
    var k2 = b[idx['date']] + ' ' + b[idx['time_ist']];
    return k1 < k2 ? -1 : 1;
  });

  var tmpl = rows.filter(function (r) { return r[idx['type']] === 'template'; }).length;
  var blog = rows.filter(function (r) { return r[idx['type']] === 'blog'; }).length;
  var health = healthNote_();
  var sheetUrl = getSheet_().getParent().getUrl();

  var subject = '[SlideEgg WhatsApp] '
    + (weekly ? ('Weekly report ' + startStr + ' to ' + endStr)
              : Utilities.formatDate(now, TIMEZONE, 'dd MMM yyyy'))
    + ' — ' + rows.length + ' post' + (rows.length === 1 ? '' : 's')
    + (health.problem ? ' — NEEDS ATTENTION' : '');

  var banner = '';
  if (health.problem) {
    banner = '<div style="border-left:5px solid #C0392B;background:#FDF1EF;padding:12px 14px;margin:0 0 16px">'
      + '<div style="font:700 14px system-ui;color:#C0392B">Problem: ' + esc_(health.headline) + '</div>'
      + '<div style="font:13px system-ui;color:#444;margin-top:5px">' + esc_(health.detail) + '</div></div>';
  } else if (!rows.length) {
    banner = '<div style="border-left:5px solid #B8860B;background:#FDF8EC;padding:12px 14px;margin:0 0 16px">'
      + '<div style="font:700 14px system-ui;color:#8A6D0B">No posts in this period</div>'
      + '<div style="font:13px system-ui;color:#444;margin-top:5px">The bot ran normally and '
      + 'reported no errors — SlideEgg simply published nothing new.</div></div>';
  }

  function stat(label, value, color) {
    return '<td align="center" style="padding:14px 10px;border:1px solid #DCE5EC">'
      + '<div style="font:700 26px system-ui;color:' + (color || '#222') + '">' + value + '</div>'
      + '<div style="font:12px system-ui;color:#777;margin-top:3px">' + label + '</div></td>';
  }

  var stats = '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%"><tr>'
    + stat(weekly ? 'Posts this week' : 'Posts today', rows.length, BRAND)
    + stat('Templates', tmpl)
    + stat('Blog posts', blog)
    + stat('Status', health.problem ? 'CHECK' : 'OK', health.problem ? '#C0392B' : '#1E8449')
    + '</tr></table>';

  var table = '<p style="color:#777;font:13px system-ui">No posts in this period.</p>';
  if (rows.length) {
    var head = '<tr>' + ['Date', 'Time', 'Type', 'Title'].map(function (h) {
      return '<th align="left" style="background:' + BRAND + ';color:#fff;padding:7px 10px;font:600 13px system-ui">' + h + '</th>';
    }).join('') + '</tr>';
    var body = rows.map(function (r, i) {
      var bg = i % 2 ? '#F7F9FB' : '#fff';
      return '<tr style="background:' + bg + '">'
        + '<td style="padding:6px 10px;font:13px system-ui;white-space:nowrap">' + esc_(r[idx['date']]) + '</td>'
        + '<td style="padding:6px 10px;font:13px system-ui;white-space:nowrap">' + esc_(r[idx['time_ist']]) + '</td>'
        + '<td style="padding:6px 10px;font:13px system-ui;white-space:nowrap">' + esc_(r[idx['type']]) + '</td>'
        + '<td style="padding:6px 10px;font:13px system-ui">'
        + '<a href="' + esc_(r[idx['url']]) + '" style="color:' + BRAND + ';text-decoration:none">'
        + esc_(r[idx['title']]) + '</a></td></tr>';
    }).join('');
    table = '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;border:1px solid #DCE5EC">'
      + head + body + '</table>';
  }

  var perDay = '';
  if (weekly) {
    perDay = '<h3 style="font:600 15px system-ui;margin:22px 0 8px">Day by day</h3>'
      + '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%">';
    for (var i = 0; i < 7; i++) {
      var d = fmt_(new Date(start.getTime() + i * 86400000));
      var n = rows.filter(function (r) { return r[idx['date']] === d; }).length;
      perDay += '<tr><td style="padding:4px 10px;font:13px system-ui;width:120px">' + d + '</td>'
        + '<td style="padding:4px 10px;font:13px system-ui;color:' + BRAND + '">'
        + new Array(Math.min(n, 20) + 1).join('&#9607;') + ' <span style="color:#666">' + n + '</span></td></tr>';
    }
    perDay += '</table>';
  }

  var html = '<html><body style="margin:0;background:#F4F6F8;padding:22px">'
    + '<div style="max-width:660px;margin:auto;background:#fff;border:1px solid #DCE5EC">'
    + '<div style="background:' + BRAND + ';padding:18px 22px">'
    + '<div style="font:700 19px system-ui;color:#fff">SlideEgg WhatsApp Channel</div>'
    + '<div style="font:13px system-ui;color:#CFE0EC;margin-top:2px">'
    + (weekly ? 'Weekly report · ' + startStr + ' to ' + endStr
              : 'Daily report · ' + Utilities.formatDate(now, TIMEZONE, 'EEEE, dd MMMM yyyy'))
    + '</div></div><div style="padding:22px">'
    + banner + stats + perDay
    + '<h3 style="font:600 15px system-ui;margin:22px 0 8px">Everything posted</h3>' + table
    + '<p style="font:13px system-ui;margin:22px 0 0">'
    + '<a href="' + sheetUrl + '" style="color:' + BRAND + '">Open the Google Sheet</a>'
    + ' &nbsp;·&nbsp; <a href="https://github.com/' + REPO + '/actions" style="color:' + BRAND + '">Run logs</a></p>'
    + '<p style="font:11px system-ui;color:#999;margin:14px 0 0">'
    + 'Sent automatically from the SlideEgg WhatsApp auto-poster.</p>'
    + '</div></div></body></html>';

  GmailApp.sendEmail(MAIL_TO, subject, 'This report is formatted in HTML. Sheet: ' + sheetUrl,
                     { htmlBody: html, name: 'SlideEgg WhatsApp Bot' });
  Logger.log('Sent to ' + MAIL_TO + ': ' + subject);
}
