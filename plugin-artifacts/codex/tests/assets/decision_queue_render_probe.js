const fs = require('fs');
const vm = require('vm');
let src = fs.readFileSync(process.argv[2], 'utf8');

// Minimal DOM + storage shim: enough for the IIFE to run to completion.
const store = {};
const el = () => ({ innerHTML:'', textContent:'', className:'', disabled:false,
  classList:{ toggle(){}, add(){}, remove(){} }, addEventListener(){},
  getAttribute(){ return null; }, outerHTML:'<script id="app-script"></script>' });
global.window = { localStorage: {
    getItem:k=>k in store?store[k]:null, setItem:(k,v)=>{store[k]=v;}, removeItem:k=>{delete store[k];} } };
global.document = {
  body:{ classList:{ toggle(){} } },
  getElementById: () => el(),
  querySelectorAll: () => [],
};
global.localStorage = window.localStorage;

// Capture what paint() writes and expose the internals we want to assert on.
src = src.replace('  paint();', `
  global.__probe = { renderBody, cardHtml, optionsFor, daysSince, MOOT_LETTER, items, meta, buildDocument, restoreDraft, saveDraft };
  paint();`);
// runInThisContext, not eval(): this executes a file the repo itself ships,
// and the vm API says so explicitly while sharing the globals shimmed above.
// eval() here also trips the pre-push scanner's code-injection rule, correctly —
// a test harness is not a reason to keep an eval in the tree.
vm.runInThisContext(src, { filename: process.argv[2] });

const P = global.__probe;
const old = Object.assign({}, P.items[0], { id:'old', touched:'2020-01-01', title:'Stale one' });
const today = new Date().toISOString().slice(0,10);
const ans = Object.assign({}, P.items[0], { id:'ans', selected:'A', title:'Answered one', touched:today });
const hostile = Object.assign({}, P.items[0], { id:'x', title:'<img src=x onerror=alert(1)>', touched:today });

let fail = 0;
const check = (name, cond, extra='') => { if(!cond){ console.log('FAIL:', name, extra); fail++; } else console.log('ok  :', name); };

const html = P.renderBody(P.meta, [old, ans, hostile]);
check('renders non-empty html', html.length > 500);
check('moot option present on every card',
      (html.match(new RegExp('No longer relevant','g'))||[]).length === 3);
check('moot letter is ×', html.includes('data-letter="' + P.MOOT_LETTER + '"'));
check('stale chip on 2020 item', html.includes('chip stale') && /Untouched \d+ days/.test(html));
check('no stale chip on fresh item', (html.match(/chip stale/g)||[]).length === 1);
check('answered card carries .answered class', html.includes('class="card answered"'));
check('unanswered card does not', html.includes('class="card"'));
check('fieldset+legend wraps options',
      html.includes('<fieldset class="options-group">') && html.includes('<legend class="section-label">'));
check('hostile title escaped', !html.includes('<img src=x') && html.includes('&lt;img src=x'));
check('daysSince handles junk', P.daysSince('not-a-date') === null && P.daysSince(null) === null);
check('daysSince computes', P.daysSince('2020-01-01') > 2000);

// buildDocument must carry the save bar (the bug this run fixed)
const doc = P.buildDocument(P.meta, [ans]);
check('published doc contains save bar', doc.includes('save-bar-shell') && doc.includes('id="save-btn"'));
check('published doc contains filter toggle', doc.includes('id="filter-unanswered"'));
check('published doc contains aria-live status', doc.includes('aria-live="polite"'));

// drafts round-trip
P.items[0].selected = 'B'; P.items[0].comment = 'hello';
P.saveDraft();
P.items[0].selected = null; P.items[0].comment = '';
const n = P.restoreDraft();
check('draft restores selection+comment', n === 1 && P.items[0].selected === 'B' && P.items[0].comment === 'hello');
// published answer must win over a stale draft
P.items[0].selected = 'C';
P.restoreDraft();
check('draft never overwrites a published answer', P.items[0].selected === 'C');

console.log(fail ? `\n${fail} FAILURE(S)` : '\nALL PASS');
process.exit(fail ? 1 : 0);
