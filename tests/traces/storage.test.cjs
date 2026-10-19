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
  const invalid=await storage.loadTraceIndex(config);
  assert.equal(invalid.coverage.complete,false);
  assert.match(invalid.coverage.invalid[0].reason,/ENOENT/);
  await assert.rejects(storage.loadTraceSummary('kept',config),storage.PartialTraceError);
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
      else if(kind==='index'){const r=await s.loadTraceIndex(c);assert.equal(r.coverage.complete,false);assert.match(r.coverage.invalid[0].reason,/Invalid or oversized/);}
      else {const operation=kind==='collection'?s.loadCollection('COL_pipe',c):s.loadFullTrace('pipe-raw',c);
        await assert.rejects(operation,/Invalid or oversized/);}})().catch(e=>{console.error(e);process.exitCode=1});`;
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
  const metadata=await fs.realpath(path.join(config.memoryPath,'traces','metadata',parsed.trace_id+'.json'));
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

test('corrupt metadata, missing raw and invalid edges have explicit isolated coverage',async t=>{
  const config=await fixture(t),root=path.join(config.memoryPath,'traces');
  for(const id of ['healthy','missing-raw','bad-edge'])await storage.storeTrace(trace(id),{id},config);
  const raw=(await storage.loadTraceSummary('missing-raw',config)).full_data_path;
  await fs.unlink(raw);
  await fs.writeFile(path.join(root,'metadata','broken.json'),'{broken');
  await fs.symlink(path.join(root,'never-created'),path.join(root,'metadata','dangling.json'));
  const edges=path.join(root,'correlations','bad-edge');await fs.mkdir(edges,{recursive:true});
  await fs.writeFile(path.join(edges,'incident.json'),JSON.stringify({trace_id:'different',incident_id:'incident'}));
  const result=await storage.loadTraceResults(config);
  assert.deepEqual(result.traces.map(t=>t.trace_id).sort(),['bad-edge','healthy']);
  assert.equal(result.coverage.complete,false);assert.equal(result.coverage.invalid.length,4);
  for(const id of ['broken','dangling','missing-raw','bad-edge'])assert.ok(result.coverage.invalid.some(v=>v.trace_id===id));
  assert.equal(JSON.parse(JSON.stringify(result)).coverage.invalid.length,4);
  const index=await storage.loadTraceIndex(config);
  assert.equal(index.total_traces,2);assert.equal(index.coverage.invalid.length,4);
  assert.equal((await storage.getTraceStats(config)).coverage.complete,false);
  await assert.rejects(storage.loadAllTraces(config),error=>{
    assert.ok(error instanceof storage.PartialTraceError);assert.equal(error.traces.length,2);
    assert.equal(error.coverage.invalid.length,4);return true;
  });
  for(const id of ['broken','dangling','missing-raw','bad-edge']){
    await assert.rejects(storage.loadTraceSummary(id,config),storage.PartialTraceError);
  }
  assert.deepEqual(await storage.loadFullTrace('healthy',config),{id:'healthy'});
  await storage.correlateTraceToIncident('healthy','healthy-incident',config);
  assert.deepEqual((await storage.loadTraceSummary('healthy',config)).incident_ids,['healthy-incident']);
  // Lazy raw JSON validation fails only the requested payload, not its neighbor.
  const rawHealthy=(await storage.loadTraceSummary('healthy',config)).full_data_path;
  await fs.writeFile(rawHealthy,'{corrupt raw');
  await assert.rejects(storage.loadFullTrace('healthy',config),SyntaxError);
  assert.equal((await storage.loadTraceSummary('healthy',config)).trace_id,'healthy');
});

test('direct ID lookup does not scan unrelated metadata or correlation directories',async t=>{
  const config=await fixture(t),root=path.join(config.memoryPath,'traces');
  await storage.storeTrace(trace('target'),{target:true},config);
  await storage.storeTrace(trace('other'),undefined,config);
  await fs.writeFile(path.join(root,'metadata','other.json'),'{broken');
  const originalReadDir=fs.readdir,originalOpen=fs.open;let opened=0;
  fs.readdir=async function(file,...args){
    if(file===path.join(root,'metadata')||file===path.join(root,'correlations'))throw new Error('unrelated corpus traversal');
    return originalReadDir.call(this,file,...args);
  };
  fs.open=async function(file,...args){if(String(file).includes('/metadata/'))opened++;return originalOpen.call(this,file,...args);};
  t.after(()=>{fs.readdir=originalReadDir;fs.open=originalOpen;});
  assert.equal((await storage.loadTraceSummary('target',config)).trace_id,'target');assert.equal(opened,1);
});

test('generated IDs use UUID entropy even with a frozen clock and Math.random',()=>{
  const OriginalDate=Date,originalRandom=Math.random;
  global.Date=class extends OriginalDate {constructor(...args){super(...(args.length?args:[1700000000000]));}};
  Math.random=()=>0.125;
  try{
    for(const generate of [storage.generateTraceId,storage.generateCollectionId]){
      const ids=Array.from({length:100},generate);assert.equal(new Set(ids).size,100);
      for(const id of ids)assert.match(id,/_[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
    }
  }finally{global.Date=OriginalDate;Math.random=originalRandom;}
});

test('compiled package root exposes explicit partial-result adoption API',()=>{
  const api=require('../../dist/src/index.js');
  assert.equal(api.loadTraceResults,storage.loadTraceResults);
  assert.equal(api.PartialTraceError,storage.PartialTraceError);
});

test('canonical direct lookup survives a corrupt legacy aggregate with explicit local coverage',async t=>{
  const config=await fixture(t),root=path.join(config.memoryPath,'traces');
  await storage.storeTrace(trace('healthy'),{canonical:true},config);
  const legacy=await storage.loadTraceIndex(config);
  legacy.traces.healthy.incident_ids=['legacy-incident'];
  await fs.writeFile(path.join(root,'index.json'),'{corrupt legacy aggregate');
  const direct=await storage.loadTraceSummary('healthy',config);
  assert.equal(direct.trace_id,'healthy');assert.equal(direct.storage_coverage.complete,false);
  assert.equal(direct.storage_coverage.scope,'canonical_trace_and_raw_availability');
  assert.equal(direct.storage_coverage.omitted[0].path,path.join(root,'index.json'));
  assert.deepEqual(await storage.loadFullTrace('healthy',config),{canonical:true});
  assert.equal((await storage.loadTraceResults(config)).coverage.complete,false);
  await storage.storeTrace(direct,{updated:true},config);
  assert.equal(JSON.parse(await fs.readFile(path.join(root,'metadata','healthy.json'),'utf8')).storage_coverage,undefined);
  await fs.writeFile(path.join(root,'index.json'),JSON.stringify(legacy));
  const merged=await storage.loadTraceSummary('healthy',config);
  assert.deepEqual(merged.incident_ids,['legacy-incident']);assert.equal(merged.storage_coverage,undefined);
  assert.deepEqual(await storage.loadFullTrace('healthy',config),{updated:true});
});

test('orphan correlation records are explicit invalid coverage without blocking healthy direct reads',async t=>{
  const config=await fixture(t),root=path.join(config.memoryPath,'traces');
  await storage.storeTrace(trace('healthy'),undefined,config);
  const directory=path.join(root,'correlations','orphan');await fs.mkdir(directory,{recursive:true});
  await fs.writeFile(path.join(directory,'incident.json'),JSON.stringify({trace_id:'orphan',incident_id:'incident'}));
  const result=await storage.loadTraceResults(config);
  assert.equal(result.coverage.complete,false);assert.equal(result.coverage.invalid.length,1);
  assert.match(result.coverage.invalid[0].reason,/absent or invalid trace metadata/);
  assert.equal((await storage.loadTraceSummary('healthy',config)).trace_id,'healthy');
  await assert.rejects(storage.loadTraceSummary('orphan',config),storage.PartialTraceError);
});

test('guarded reads reject actual leaf, ancestor and restored ancestor swaps before reading bytes',{skip:process.platform==='win32'},async t=>{
  for(const kind of ['metadata','raw','legacy','collection'])for(const mode of (kind==='metadata'?['leaf','ancestor','ancestor-aba']:['leaf']))await t.test(`${kind}/${mode}`,async t=>{
    const config=await fixture(t);await storage.storeTrace(trace('race'),{safe:true},config);
    const root=await fs.realpath(path.join(config.memoryPath,'traces'));
    let target,call;
    if(kind==='metadata'){target=path.join(root,'metadata','race.json');call=()=>storage.loadTraceSummary('race',config);}
    if(kind==='raw'){target=(await storage.loadTraceSummary('race',config)).full_data_path;target=await fs.realpath(target);call=()=>storage.loadFullTrace('race',config);}
    if(kind==='legacy'){target=path.join(root,'index.json');await fs.writeFile(target,JSON.stringify(await storage.loadTraceIndex(config)));call=async()=>{const result=await storage.loadTraceIndex(config);assert.equal(result.coverage.complete,false);};}
    if(kind==='collection'){const collection=await storage.createCollection('race',['race'],undefined,config);target=path.join(root,'collections',collection.collection_id+'.json');call=()=>storage.loadCollection(collection.collection_id,config);}
    const outside=await fs.mkdtemp(path.join(os.tmpdir(),'trace-outside-race-'));t.after(()=>fs.rm(outside,{recursive:true,force:true}));
    // Outside data is valid and deliberately resembles the requested record.
    await fs.copyFile(target,path.join(outside,path.basename(target)));
    const originalOpen=fs.open,parent=path.dirname(target),backup=parent+'.held';let attacked=false,outsideReads=0,swapped=false;
    fs.open=async function(file,...args){
      if(file!==target||attacked)return originalOpen.call(this,file,...args);
      attacked=true;
      if(mode==='leaf'){await fs.rename(target,target+'.held');await fs.symlink(path.join(outside,path.basename(target)),target);}
      else{await fs.rename(parent,backup);await fs.symlink(outside,parent);swapped=true;}
      const handle=await originalOpen.call(this,file,...args);
      const originalRead=handle.read.bind(handle);handle.read=async(...args)=>{outsideReads++;return originalRead(...args);};
      if(mode==='ancestor-aba'){await fs.unlink(parent);await fs.rename(backup,parent);swapped=false;}
      return handle;
    };
    try{
      if(kind==='legacy')await call();else await assert.rejects(call());
      assert.equal(attacked,true,'replacement must occur at the real open boundary');
      assert.equal(outsideReads,0,'outside descriptor must be rejected before consuming bytes');
    }finally{
      fs.open=originalOpen;
      if(mode==='leaf'&&attacked){await fs.unlink(target);await fs.rename(target+'.held',target);}
      if(swapped){await fs.unlink(parent);await fs.rename(backup,parent);}
    }
  });
});
