/**
 * Tests the pure logic inside Code.gs by loading it with the Google-specific
 * globals stubbed out. Apps Script cannot be run here, but the CSV parsing,
 * date filtering and health rules are ordinary JavaScript and are exactly
 * where a bug would silently produce a wrong report.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let fails = [];
const check = (n, c, x) => {
  console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${!c && x !== undefined ? '  -> ' + JSON.stringify(x) : ''}`);
  if (!c) fails.push(n);
};

// ---- stub the Apps Script services the file touches at load time
const sent = [];
let FETCH = {};

const sandbox = {
  Logger: { log: () => {} },
  UrlFetchApp: {
    fetch: (url) => {
      const key = Object.keys(FETCH).find(k => url.includes(k));
      const body = key ? FETCH[key] : null;
      return {
        getResponseCode: () => (body === null ? 404 : 200),
        getContentText: () => body,
      };
    },
  },
  PropertiesService: {
    getScriptProperties: () => ({ getProperty: () => 'fake-id', setProperty: () => {} }),
  },
  SpreadsheetApp: {
    openById: () => ({
      getSheets: () => [{ getParent: () => ({ getUrl: () => 'https://docs.google.com/SHEET' }) }],
    }),
  },
  GmailApp: { sendEmail: (to, subject, body, opts) => sent.push({ to, subject, opts }) },
  Utilities: {
    formatDate: (d, tz, fmt) => {
      const p = (n) => String(n).padStart(2, '0');
      if (fmt === 'yyyy-MM-dd') return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
      return d.toISOString();
    },
  },
  ScriptApp: { getProjectTriggers: () => [], WeekDay: { MONDAY: 1 } },
  Date, Math, JSON, String, Array, Object, isNaN, parseInt, console,
};

const code = fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8');
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

// ---- CSV parsing
console.log('\nCSV PARSING');
const p = sandbox.parseCsv_;
check('simple rows', JSON.stringify(p('a,b\n1,2\n')) === '[["a","b"],["1","2"]]');
check('quoted comma kept in one field',
  p('a,b\n"Hello, World",2\n')[1][0] === 'Hello, World');
check('escaped quote inside a field',
  p('a\n"He said ""hi"""\n')[1][0] === 'He said "hi"');
check('newline inside a quoted field',
  p('a,b\n"line1\nline2",x\n')[1][0] === 'line1\nline2');
check('CRLF handled', JSON.stringify(p('a,b\r\n1,2\r\n')) === '[["a","b"],["1","2"]]');
check('no trailing newline still parsed', p('a,b\n1,2')[1][1] === '2');
check('empty input -> empty', p('').length === 0);
check('real title with comma survives',
  p('date,title\n2026-08-20,"Data Storytelling, Simplified"\n')[1][1]
  === 'Data Storytelling, Simplified');

// ---- health rules
console.log('\nHEALTH RULES');
const iso = (hoursAgo) => new Date(Date.now() - hoursAgo * 3600000).toISOString();

function setState(run, health) {
  FETCH = {
    'state/last_run.json': JSON.stringify(run),
    'state/health.json': JSON.stringify(health),
  };
}

setState({ run: iso(0.5), mode: 'live', failed: 0 }, { last_new_item_at: iso(2) });
check('healthy -> no problem', sandbox.healthNote_().problem === false);

setState({ run: iso(0.5), mode: 'live', failed: 3, failed_urls: ['u1'] }, { last_new_item_at: iso(1) });
check('failed sends -> problem', sandbox.healthNote_().problem === true);
check('failure count in the headline', /3 post/.test(sandbox.healthNote_().headline));

setState({ run: iso(0.5), mode: 'dry', why_dry: 'WHAPI_TOKEN missing/empty', failed: 0 }, {});
check('preview mode -> problem', sandbox.healthNote_().problem === true);
check('preview reason surfaced', /WHAPI_TOKEN/.test(sandbox.healthNote_().detail));

setState({ run: iso(0.5), mode: 'live', failed: 0 }, { last_new_item_at: iso(40) });
check('40h with nothing new -> problem (the two-day outage)',
  sandbox.healthNote_().problem === true);
check('points at the stale page', /page1_top3/.test(sandbox.healthNote_().detail));

setState({ run: iso(9), mode: 'live', failed: 0 }, { last_new_item_at: iso(1) });
check('bot silent for 9h -> problem', sandbox.healthNote_().problem === true);

FETCH = {};
check('no data at all -> problem, not a false OK', sandbox.healthNote_().problem === true);

// ---- report contents
console.log('\nREPORT CONTENT');
const today = sandbox.fmt_(new Date());
const yday = sandbox.fmt_(new Date(Date.now() - 86400000));
const old = sandbox.fmt_(new Date(Date.now() - 9 * 86400000));

FETCH = {
  'state/posts.csv':
    'date,time_ist,year,month,month_name,week,day_name,type,title,url,image_url\n'
    + `${today},09:00,2026,8,August,W34,Thu,template,"Alpha, First",https://x/a,\n`
    + `${today},10:00,2026,8,August,W34,Thu,blog,Beta Guide,https://x/b,\n`
    + `${yday},11:00,2026,8,August,W34,Wed,template,Gamma,https://x/c,\n`
    + `${old},11:00,2026,8,August,W33,Tue,template,TooOld,https://x/d,\n`,
  'state/last_run.json': JSON.stringify({ run: iso(0.5), mode: 'live', failed: 0 }),
  'state/health.json': JSON.stringify({ last_new_item_at: iso(1) }),
};

sent.length = 0;
sandbox.sendReport_(false);
let m = sent[0];
check('daily: one email sent', sent.length === 1);
check('daily: goes to admin@slideegg.com', m.to === 'admin@slideegg.com');
check('daily: counts only today', /— 2 posts/.test(m.subject), m.subject);
check('daily: no alarm when healthy', !/NEEDS ATTENTION/.test(m.subject));
check('daily: title with a comma is intact', /Alpha, First/.test(m.opts.htmlBody));
check('daily: excludes yesterday', !/Gamma/.test(m.opts.htmlBody));
check('daily: sheet link present', /docs\.google\.com\/SHEET/.test(m.opts.htmlBody));
check('daily: sent under a clear name', m.opts.name === 'SlideEgg WhatsApp Bot');

sent.length = 0;
sandbox.sendReport_(true);
m = sent[0];
check('weekly: includes today and yesterday', /Gamma/.test(m.opts.htmlBody) && /Beta Guide/.test(m.opts.htmlBody));
check('weekly: excludes the 9-day-old row', !/TooOld/.test(m.opts.htmlBody));
check('weekly: counts 3', /— 3 posts/.test(m.subject), m.subject);
check('weekly: has a day-by-day section', /Day by day/.test(m.opts.htmlBody));

// alarm path
FETCH['state/health.json'] = JSON.stringify({ last_new_item_at: iso(50) });
sent.length = 0;
sandbox.sendReport_(false);
check('alarm reaches the subject line', /NEEDS ATTENTION/.test(sent[0].subject), sent[0].subject);

// empty csv
FETCH['state/posts.csv'] = 'date,time_ist,year,month,month_name,week,day_name,type,title,url,image_url\n';
FETCH['state/health.json'] = JSON.stringify({ last_new_item_at: iso(1) });
sent.length = 0;
sandbox.sendReport_(false);
check('no posts -> still sends, reported as normal',
  sent.length === 1 && /0 posts/.test(sent[0].subject)
  && /No posts in this period/.test(sent[0].opts.htmlBody));

// html injection
FETCH['state/posts.csv'] =
  'date,time_ist,year,month,month_name,week,day_name,type,title,url,image_url\n'
  + `${today},09:00,2026,8,August,W34,Thu,template,"<script>bad()</script>",https://x/a,\n`;
sent.length = 0;
sandbox.sendReport_(false);
check('html in a title is escaped',
  /&lt;script&gt;/.test(sent[0].opts.htmlBody) && !/<script>bad/.test(sent[0].opts.htmlBody));

console.log(fails.length ? `\nFAILURES: ${JSON.stringify(fails)}` : '\nALL PASS');
process.exit(fails.length ? 1 : 0);
