const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
const storage = require('../../dist/src/traces/storage.js');
const modulePath = path.resolve('dist/src/traces/storage.js');
const fixture = async t => {
  const memoryPath = await fs.mkdtemp(path.join(os.tmpdir(), 'trace-storage-'));
  t.after(() => fs.rm(memoryPath, { recursive:true, force:true }));
  return {storageMode:'local',memoryPath,autoMine:false,defaultSimilarityThreshold:0.7,defaultMaxResults:10};
};
const trace = (id, source='internal') => ({trace_id:id, source,timestamp:1700000000000,severity:'info',category:'custom',operation:'fixture',summary:{description:'test',highlights:[]},has_full_data:false,tokens_estimated:1});
const child = (code, args) => new Promise((resolve,reject) => {
  const p = spawn(process.execPath,['-e',code,...args],{stdio:['ignore','ignore','pipe']});
  let error='',timedOut=false;p.stderr.on('data',chunk=>error=(error+chunk).slice(-65536));
  const timer=setTimeout(()=>{timedOut=true;p.kill('SIGKILL');},5000);
  p.on('error',e=>{clearTimeout(timer);reject(e);});
  p.on('close',status=>{clearTimeout(timer);status===0&&!timedOut?resolve():reject(new Error(timedOut?'child timed out':error));});
});
test('raw falsy values and summary-only availability survive process restart',async t => {
  const config = await fixture(t);
  for(const [id,raw] of [['false',false],['zero',0],['null',null],['empty','']]) {
    await storage.storeTrace(trace(id),raw,config);
    assert.deepEqual(await storage.loadFullTrace(id,config),raw);
    assert.equal((await storage.loadTraceSummary(id,config)).has_full_data,true);
  }
  await storage.storeTrace(trace('summary'),undefined,config);
  assert.equal((await storage.loadTraceSummary('summary',config)).has_full_data,false);
  await child(`const s=require(process.argv[1]); const c=JSON.parse(process.argv[2]); s.loadFullTrace('false',c).then(v=>{if(v!==false)process.exit(2)})`,[modulePath,JSON.stringify(config)]);
});
test('competing processes and repeated IDs preserve every committed trace',async t => {
  const config = await fixture(t);
  const code=`const s=require(process.argv[1]),c=JSON.parse(process.argv[2]),t=JSON.parse(process.argv[3]);s.storeTrace(t,{id:t.trace_id},c).catch(e=>{console.error(e);process.exit(1)})`;
  await Promise.all(Array.from({length:8},(_,i)=>child(code,[modulePath,JSON.stringify(config),JSON.stringify(trace('id'+i))])));
  await Promise.all(Array.from({length:8},(_,i)=>storage.storeTrace(trace('same'),{revision:i},config)));
  const index = await storage.loadTraceIndex(config);
  assert.equal(index.total_traces,9);
  assert.equal(index.by_source.internal.length,9);
  assert.equal(new Set(index.by_source.internal).size,9);
  assert.equal(typeof (await storage.loadFullTrace('same',config)).revision,'number');
});
test('traversal, invalid source and escaping symlinks are rejected',async t => {
  const config = await fixture(t);
  await assert.rejects(storage.storeTrace(trace('../escape'),{},config));
  await assert.rejects(storage.storeTrace(trace('safe','../../outside'),{},config));
  await assert.rejects(storage.loadCollection('../outside',config));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(),'outside-traces-'));
  t.after(()=>fs.rm(outside,{recursive:true,force:true}));
  await fs.mkdir(path.join(config.memoryPath,'traces'),{recursive:true});
  await fs.symlink(outside,path.join(config.memoryPath,'traces','raw'));
  await assert.rejects(storage.storeTrace(trace('safe'),{},config),/escapes/);
  assert.deepEqual(await fs.readdir(outside),[]);
});
test('unpublished crash remnants are ignored; published missing raw is explicit corruption',async t => {
  const config = await fixture(t);
  await storage.storeTrace(trace('kept'),{ok:true},config);
  const metadata=path.join(config.memoryPath,'traces','metadata');
  await fs.writeFile(path.join(metadata,'interrupted.json.dead.tmp'),'{broken');
  assert.equal((await storage.loadTraceIndex(config)).total_traces,1);
  const kept=await storage.loadTraceSummary('kept',config);
  await fs.unlink(kept.full_data_path);
  await assert.rejects(storage.loadTraceIndex(config),/ENOENT/);
});
test('legacy index recovers only known raw location and ignores injected locator',async t => {
  const config = await fixture(t);
  const root=path.join(config.memoryPath,'traces');
  await fs.mkdir(path.join(root,'raw','internal'),{recursive:true});
  await fs.writeFile(path.join(root,'raw','internal','legacy.json'),'false');
  const entry={trace_id:'legacy',source:'internal',timestamp:1700000000000,severity:'info',category:'custom',operation:'old',summary_preview:'old',has_error:false,incident_ids:[],full_data_path:'/etc/passwd'};
  await fs.writeFile(path.join(root,'index.json'),JSON.stringify({traces:{legacy:entry}}));
  assert.equal(await storage.loadFullTrace('legacy',config),false);
  const collection=await storage.createCollection('sample',['legacy'],undefined,config);
  assert.equal(collection.summary.total_traces,1);
  assert.equal((await storage.loadCollection(collection.collection_id,config)).name,'sample');
});

test('malformed public inputs are rejected before publication without poisoning existing traces',async t => {
  const config=await fixture(t);
  await storage.storeTrace(trace('valid'),{valid:true},config);
  for(const patch of [{summary:null},{summary:{description:5,highlights:[]}}, {severity:'other'},
      {category:'other'},{incident_ids:[null]},{operation:5},{tokens_estimated:NaN},{duration_ms:-1}]) {
    await assert.rejects(storage.storeTrace({...trace('bad'),...patch},{bad:true},config));
    assert.deepEqual(await storage.loadFullTrace('valid',config),{valid:true});
    assert.equal((await storage.loadTraceIndex(config)).total_traces,1);
  }
});

test('log scan retains top K and reports incomplete coverage explicitly',async t => {
  const {readProjectLogs}=require('../../dist/src/log-reader.js');
  const config=await fixture(t);
  const file=path.join(config.memoryPath,'logs.jsonl');
  await fs.writeFile(file,Array.from({length:2000},(_,i)=>JSON.stringify({timestamp:1700000000000+i,level:'info',message:'message '+i})).join('\n'));
  const result=await readProjectLogs({source:'path',path:file,limit:5});
  assert.equal(result.entries.length,5);assert.equal(result.total_matched,2000);
  assert.equal(result.entries[0].message,'message 1999');assert.equal(result.coverage.complete,true);
  await fs.writeFile(file,'x'.repeat(1024*1024+1));
  const oversized=await readProjectLogs({source:'path',path:file});
  assert.equal(oversized.coverage.complete,false);assert.equal(oversized.truncated,true);
  assert.match(oversized.coverage.skipped_files[0].reason,/1 MiB/);
  await assert.rejects(readProjectLogs({source:'path',path:file,limit:Infinity}),/limit/);
});

test('canonical OpenTelemetry highlights survive accepted storage and malformed highlights fail',async t=>{
  const config=await fixture(t);
  const {OpenTelemetryAdapter}=require('../../dist/src/traces/adapters/opentelemetry.js');
  const input={resourceSpans:[{resource:{attributes:[{key:'service.name',value:{stringValue:'audit-fixture'}}]},
    scopeSpans:[{spans:[{name:'GET /health',traceId:'abc',spanId:'def',startTimeUnixNano:'1700000000000000000',
      endTimeUnixNano:'1700000000001000000',attributes:[]}]}]}]};
  const [parsed]=await new OpenTelemetryAdapter().parse(input);
  assert.ok(parsed.summary.highlights.length);
  assert.ok(parsed.summary.highlights.every(h=>typeof h.label==='string'&&typeof h.value==='string'));
  await storage.storeTrace(parsed,input,config);
  assert.equal((await storage.loadTraceSummary(parsed.trace_id,config)).operation,'GET /health');
  assert.deepEqual(await storage.loadFullTrace(parsed.trace_id,config),input);
  for(const highlights of [['old string'],[null],[{label:'x',value:2}],[{value:'missing label'}]]){
    await assert.rejects(storage.storeTrace({...trace('bad-highlight'),summary:{description:'bad',highlights}},undefined,config),/shape/);
  }
});

test('public readers reject actual FIFOs without waiting for a writer', {skip:process.platform==='win32'&&'mkfifo is POSIX-only'},async t=>{
  const logModule=path.resolve('dist/src/log-reader.js');
  for(const kind of ['log','index','collection','raw']){
    const config=await fixture(t),root=path.join(config.memoryPath,'traces');
    await fs.mkdir(path.join(root,'collections'),{recursive:true});
    let pipe=path.join(root,kind==='index'?'index.json':kind==='collection'?'collections/COL_pipe.json':'pipe');
    if(kind==='raw'){
      await storage.storeTrace(trace('pipe-raw'),{ok:true},config);
      pipe=(await storage.loadTraceSummary('pipe-raw',config)).full_data_path;
      await fs.unlink(pipe);
    }
    execFileSync('mkfifo',[pipe]);
    const code=`const assert=require('node:assert/strict'),s=require(process.argv[1]),c=JSON.parse(process.argv[2]),kind=process.argv[3];
      (async()=>{if(kind==='log'){const r=await require(process.argv[5]).readProjectLogs({source:'path',path:process.argv[4]});
        assert.equal(r.coverage.complete,false);assert.match(r.coverage.skipped_files[0].reason,/regular file/);}
      else {const operation=kind==='index'?s.loadTraceIndex(c):kind==='collection'?s.loadCollection('COL_pipe',c):s.loadFullTrace('pipe-raw',c);
        await assert.rejects(operation,/Invalid or oversized trace file/);}})().catch(e=>{console.error(e);process.exitCode=1});`;
    await child(code,[modulePath,JSON.stringify(config),kind,pipe,logModule]);
  }
});

test('collections enforce the same bounded atomic publication and read budget',async t=>{
  const config=await fixture(t);
  await assert.rejects(storage.createCollection('x'.repeat(1024*1024),[],undefined,config),/1 MiB/);
  const directory=path.join(config.memoryPath,'traces','collections');
  assert.deepEqual(await fs.readdir(directory),[]);
  await fs.writeFile(path.join(directory,'COL_large.json'),'x'.repeat(1024*1024+1));
  await assert.rejects(storage.loadCollection('COL_large',config),/oversized/);
});

test('correlation edges survive reload, trace replacement and competing processes',async t=>{
  const config=await fixture(t);
  await storage.storeTrace(trace('correlated'),undefined,config);
  await storage.correlateTraceToIncident('correlated','first',config);
  assert.deepEqual((await storage.loadTracesForIncident('first',config)).map(t=>t.trace_id),['correlated']);
  const code=`const s=require(process.argv[1]),c=JSON.parse(process.argv[2]);
    s.correlateTraceToIncident('correlated',process.argv[3],c).catch(e=>{console.error(e);process.exitCode=1});`;
  await Promise.all(Array.from({length:8},(_,i)=>child(code,[modulePath,JSON.stringify(config),'incident'+i])));
  await storage.storeTrace(trace('correlated'),undefined,config);
  await storage.correlateTraceToIncident('correlated','first',config);
  const index=await storage.loadTraceIndex(config);
  assert.equal(index.traces.correlated.incident_ids.length,9);
  for(const id of ['first',...Array.from({length:8},(_,i)=>'incident'+i)])assert.deepEqual(index.by_incident[id],['correlated']);
  assert.equal(index.total_traces,1);
  await storage.correlateTraceToIncident('unknown','valid',config);
  assert.equal((await storage.loadTracesForIncident('valid',config)).length,0);
  for(const id of ['../escape','__proto__']){
    await assert.rejects(storage.correlateTraceToIncident('correlated',id,config),/Invalid storage ID/);
    await assert.rejects(storage.correlateTraceToIncident(id,'valid',config),/Invalid storage ID/);
    await assert.rejects(storage.loadTracesForIncident(id,config),/Invalid storage ID/);
  }
});

test('legacy correlations survive metadata projection and escaping correlation directories are rejected',async t=>{
  const config=await fixture(t),root=path.join(config.memoryPath,'traces');
  await storage.storeTrace(trace('legacy-correlated'),undefined,config);
  const index=await storage.loadTraceIndex(config);
  index.traces['legacy-correlated'].incident_ids=['old-incident'];
  await fs.writeFile(path.join(root,'index.json'),JSON.stringify(index));
  await storage.correlateTraceToIncident('legacy-correlated','new-incident',config);
  assert.deepEqual(new Set((await storage.loadTraceSummary('legacy-correlated',config)).incident_ids),new Set(['old-incident','new-incident']));
  const outside=await fs.mkdtemp(path.join(os.tmpdir(),'outside-correlations-'));
  t.after(()=>fs.rm(outside,{recursive:true,force:true}));
  await fs.rm(path.join(root,'correlations'),{recursive:true});
  await fs.symlink(outside,path.join(root,'correlations'));
  await assert.rejects(storage.correlateTraceToIncident('legacy-correlated','escape',config),/escapes/);
  assert.deepEqual(await fs.readdir(outside),[]);
});

test('all retrieval views preserve canonical OTLP summaries in one metadata pass',async t=>{
  const config=await fixture(t);
  const {OpenTelemetryAdapter}=require('../../dist/src/traces/adapters/opentelemetry.js');
  const [parsed]=await new OpenTelemetryAdapter().parse({resourceSpans:[{
    resource:{attributes:[{key:'service.name',value:{stringValue:'fidelity-fixture'}}]},scopeSpans:[{spans:[{
      name:'GET /health '+ 'detail '.repeat(30),traceId:'abc',spanId:'def',startTimeUnixNano:'1700000000000000000',
      endTimeUnixNano:'1700000006001000000',status:{code:2,message:'upstream unavailable'},attributes:[],
    }]}]}]});
  parsed.tokens_estimated=73;
  parsed.session_id='fidelity-session';parsed.relevance_score=0.8;
  await storage.storeTrace(parsed,{raw:true},config);
  await storage.correlateTraceToIncident(parsed.trace_id,'fidelity-incident',config);
  assert.ok(parsed.summary.highlights.length);assert.ok(parsed.summary.error);assert.ok(parsed.summary.performance);
  const canonicalSummary=JSON.parse(JSON.stringify(parsed.summary));
  const canonicalTrace={...JSON.parse(JSON.stringify(parsed)),incident_ids:['fidelity-incident']};
  const metadata=path.join(config.memoryPath,'traces','metadata',parsed.trace_id+'.json');
  const originalOpen=fs.open;let reads=0;
  fs.open=async function(file,...args){if(file===metadata)reads++;return originalOpen.call(this,file,...args);};
  t.after(()=>{fs.open=originalOpen;});
  const calls=[()=>storage.loadTraceSummary(parsed.trace_id,config),()=>storage.loadAllTraces(config),
    ()=>storage.loadTracesBySource(parsed.source,config),()=>storage.loadTracesByCategory(parsed.category,config),
    ()=>storage.loadTracesBySeverity(parsed.severity,config),()=>storage.loadTracesForIncident('fidelity-incident',config)];
  for(const call of calls){
    reads=0;const result=await call(),loaded=Array.isArray(result)?result[0]:result;
    assert.deepEqual(loaded,canonicalTrace);
    assert.equal(reads,1);assert.deepEqual(loaded.summary,canonicalSummary);
    assert.equal(loaded.duration_ms,parsed.duration_ms);assert.equal(loaded.tokens_estimated,73);
    assert.equal(loaded.external_id,'abc');assert.equal(loaded.has_full_data,true);
    assert.equal(loaded.full_data_path,parsed.full_data_path);assert.deepEqual(loaded.incident_ids,['fidelity-incident']);
  }
  reads=0;
  const collection=await storage.createCollection('canonical durations',[parsed.trace_id,parsed.trace_id],undefined,config);
  assert.equal(reads,1);assert.equal(collection.summary.slowest_operation,parsed.operation);
  assert.deepEqual(await storage.loadFullTrace(parsed.trace_id,config),{raw:true});
});
