const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
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
  let error='';p.stderr.on('data',chunk=>error+=chunk);
  p.on('error',reject);p.on('exit',status=>status===0?resolve():reject(new Error(error)));
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
