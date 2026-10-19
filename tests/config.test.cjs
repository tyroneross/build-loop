const { test } = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const { getConfig, getTokenConfig } = require('../dist/src/config.js');
const names = ['MODE', 'PATH', 'AUTO_MINE', 'THRESHOLD', 'MAX_RESULTS', 'TOKEN_BUDGET', 'TIER', 'AUTO_ADJUST'].map(name => `CLAUDE_MEMORY_${name}`);
function environment(values, check) {
  const previous = Object.fromEntries(names.map(name => [name, process.env[name]]));
  try {
    for (const name of names) delete process.env[name];
    for (const [name, value] of Object.entries(values)) process.env[`CLAUDE_MEMORY_${name}`] = value;
    check();
  } finally {
    for (const name of names) {
      if (previous[name] === undefined) delete process.env[name];
      else process.env[name] = previous[name];
    }
  }
}
const budget = () => ({total: 10, allocated: {patterns: 0, incidents: 10, metadata: 0}, perItem: {pattern: 1, incident: 2, summary: 1}});
test('defaults and empty environment values preserve configuration', () => {
  environment({}, () => {
    assert.deepEqual(getConfig(), {storageMode:'local',memoryPath:path.join(process.cwd(),'.claude/memory'),autoMine:false,defaultSimilarityThreshold:0.5,defaultMaxResults:5});
    assert.equal(getTokenConfig().budget.total,2500);
    assert.equal(getTokenConfig().autoAdjust,true);
    assert.equal(getTokenConfig().defaultTier,'compact');
  });
  environment(Object.fromEntries(names.map(name => [name.replace('CLAUDE_MEMORY_', ''),''])), () => {
    assert.equal(getConfig().defaultMaxResults,5);
    assert.equal(getTokenConfig().budget.total,2500);
    assert.equal(getTokenConfig().autoAdjust,true);
  });
});
test('valid env, boundary values and shared-path override are supported', () => {
  environment({MODE:'shared',AUTO_MINE:'true',THRESHOLD:'1e0',MAX_RESULTS:'0',TOKEN_BUDGET:'1',TIER:'full',AUTO_ADJUST:'false'}, () => {
    assert.equal(getConfig().memoryPath,path.join(os.homedir(),'.claude-memory'));
    assert.equal(getConfig().defaultMaxResults,0);
    assert.equal(getConfig().autoMine,true);
    assert.equal(getTokenConfig().budget.total,1);
    assert.equal(getTokenConfig().autoAdjust,false);
    assert.equal(getTokenConfig().defaultTier,'full');
    assert.equal(getConfig({storageMode:'local'}).memoryPath,path.join(process.cwd(),'.claude/memory'));
    assert.equal(getConfig({defaultSimilarityThreshold:0,defaultMaxResults:Number.MAX_SAFE_INTEGER}).defaultMaxResults,Number.MAX_SAFE_INTEGER);
  });
});
test('malformed numeric environment values fail whole-value validation', () => {
  for (const value of ['NaN','Infinity','-Infinity','5suffix','1.0suffix','0x10',' ','9007199254740992']) {
    for (const name of ['MAX_RESULTS','TOKEN_BUDGET']) environment({[name]:value}, () => assert.throws(name === 'MAX_RESULTS' ? getConfig : getTokenConfig,TypeError));
  }
  for (const value of ['NaN','Infinity','0.5suffix','-0.1','1.01',' ']) environment({THRESHOLD:value}, () => assert.throws(getConfig,TypeError));
  for (const name of ['MAX_RESULTS','TOKEN_BUDGET']) environment({[name]:'1.5'}, () => assert.throws(name === 'MAX_RESULTS' ? getConfig : getTokenConfig,TypeError));
  environment({TOKEN_BUDGET:'0'}, () => assert.throws(getTokenConfig,TypeError));
});
test('memory overrides validate runtime types and paths', () => {
  environment({}, () => {
    for (const overrides of [{storageMode:'other'}, {memoryPath:''}, {memoryPath:' '}, {memoryPath:'bad\0path'}, {memoryPath:42}, {autoMine:'false'}, {defaultSimilarityThreshold:NaN}, {defaultSimilarityThreshold:Infinity}, {defaultSimilarityThreshold:-1}, {defaultSimilarityThreshold:2}, {defaultMaxResults:-1}, {defaultMaxResults:1.5}, {defaultMaxResults:'5'}]) assert.throws(() => getConfig(overrides),TypeError);
    assert.equal(getConfig({memoryPath:'relative/path',autoMine:true}).memoryPath,'relative/path');
  });
});
test('invalid enum and boolean env values are rejected after explicit overrides merge', () => {
  environment({MODE:'bad',PATH:' ',AUTO_MINE:'yes',THRESHOLD:'bad',MAX_RESULTS:'bad'}, () => {
    assert.throws(getConfig,TypeError);
    assert.equal(getConfig({storageMode:'shared',memoryPath:'/valid',autoMine:false,defaultSimilarityThreshold:0,defaultMaxResults:0}).storageMode,'shared');
  });
  environment({TOKEN_BUDGET:'bad',TIER:'bad',AUTO_ADJUST:'yes'}, () => {
    assert.throws(getTokenConfig,TypeError);
    assert.deepEqual(getTokenConfig({budget:budget(),defaultTier:'summary',autoAdjust:false}),{budget:budget(),defaultTier:'summary',autoAdjust:false});
  });
  for (const values of [{AUTO_MINE:'False'},{MODE:'other'},{AUTO_ADJUST:'0'},{TIER:'other'}]) environment(values, () => assert.throws('AUTO_MINE' in values || 'MODE' in values ? getConfig : getTokenConfig,TypeError));
});
test('token overrides reject malformed nested budgets, overflow and invalid per-item limits', () => {
  environment({}, () => {
    for (const malformed of [null,{}, {total:10}, {...budget(),total:0}, {...budget(),total:Infinity}, {...budget(),total:Number.MAX_SAFE_INTEGER+1}, {...budget(),allocated:null}, {...budget(),perItem:[]}, {...budget(),allocated:{patterns:0,incidents:11,metadata:0}}, {...budget(),allocated:{patterns:-1,incidents:0,metadata:0}}, {...budget(),allocated:{patterns:0,incidents:0.5,metadata:0}}, {...budget(),perItem:{pattern:0,incident:2,summary:1}}, {...budget(),perItem:{pattern:1,incident:2,summary:NaN}}, {...budget(),allocated:{patterns:Number.MAX_SAFE_INTEGER,incidents:Number.MAX_SAFE_INTEGER,metadata:0}}]) assert.throws(() => getTokenConfig({budget:malformed}),TypeError);
    for (const overrides of [{defaultTier:'other'},{autoAdjust:'true'}]) assert.throws(() => getTokenConfig(overrides),TypeError);
    assert.deepEqual(getTokenConfig({budget:budget()}).budget,budget());
    const largest = {...budget(),total:Number.MAX_SAFE_INTEGER,allocated:{patterns:Number.MAX_SAFE_INTEGER,incidents:0,metadata:0}};
    assert.equal(getTokenConfig({budget:largest}).budget.total,Number.MAX_SAFE_INTEGER);
  });
});

test('undefined optional overrides inherit validated defaults and environment',()=>{
  environment({MODE:'shared',PATH:'/optional-path',AUTO_MINE:'true',THRESHOLD:'0.7',MAX_RESULTS:'3',TIER:'full',AUTO_ADJUST:'false'},()=>{
    assert.deepEqual(getConfig({storageMode:undefined,memoryPath:undefined,autoMine:undefined,defaultSimilarityThreshold:undefined,defaultMaxResults:undefined}),getConfig());
    assert.deepEqual(getTokenConfig({budget:undefined,defaultTier:undefined,autoAdjust:undefined}),getTokenConfig());
    assert.throws(()=>getConfig({memoryPath:null}),TypeError);
    assert.throws(()=>getTokenConfig({budget:null}),TypeError);
  });
});
