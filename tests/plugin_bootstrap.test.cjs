const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture(project, runtime) {
  const events = [];
  const Z = {Prefs:{get:()=>project},File:{getContentsAsync:async()=>JSON.stringify(runtime)}};
  const services = {scriptloader:{loadSubScript:(path,scope)=>{
    if(path.endsWith('import_drive_annotations.js')) scope.PAPERPILE_LOAD_ONLY.importer={};
    if(path.endsWith('engine.js')) scope.DriveAnnotationEngine=class{
      constructor(importer,config){events.push(['config',config.project]);}
      start(){events.push(['start']);}
      async stop(){events.push(['stop']);}
    };
  }}};
  const context=vm.createContext({Zotero:Z,Services:services,IOUtils:{},ChromeUtils:{},Cc:{},Ci:{}});
  vm.runInContext(fs.readFileSync(require.resolve('../plugin/bootstrap.js'),'utf8'),context);
  return {context,events,Z};
}

test('bootstrap reads the local project preference and stops the engine on shutdown',async()=>{
  const f=fixture('/test/checkout',{project:'/test/checkout'});
  await f.context.startup({rootURI:'jar:file:///test/bridge.xpi!/'});
  assert.deepEqual(f.events,[['config','/test/checkout'],['start']]);
  assert.ok(f.Z.PaperpileDriveBridge);
  await f.context.shutdown();
  assert.equal(f.events.at(-1)[0],'stop');
  assert.equal(f.Z.PaperpileDriveBridge,undefined);
});
test('bootstrap gives a clear error when installed without local setup',async()=>{
  const f=fixture(undefined,{});
  await assert.rejects(f.context.startup({rootURI:'jar:file:///test/bridge.xpi!/'}),/generated/);
});
test('moving a checkout requires rebuilding its local launchers',async()=>{
  const f=fixture('/test/new',{project:'/test/old'});
  await assert.rejects(f.context.startup({rootURI:'jar:file:///test/bridge.xpi!/'}),/Rebuild/);
});

test('the actual packaged modules share the configured project with the importer',async()=>{
  const writes=[];
  const runtime={project:'/test/configured-checkout'};
  const Z={Prefs:{get:()=>runtime.project},File:{
    getContentsAsync:async()=>JSON.stringify(runtime),
    putContentsAsync:async(path)=>writes.push(path),
  }};
  const services={scriptloader:{loadSubScript:(uri,scope)=>{
    const name=uri.split('/').at(-1);
    const file=name==='engine.js'?'../plugin/engine.js':'../scripts/import_drive_annotations.js';
    vm.runInContext(fs.readFileSync(require.resolve(file),'utf8'),vm.createContext(scope));
    if(name==='engine.js') scope.DriveAnnotationEngine.prototype.start=function(){};
  }}};
  const context=vm.createContext({Zotero:Z,Services:services,IOUtils:{},ChromeUtils:{},Cc:{},Ci:{}});
  vm.runInContext(fs.readFileSync(require.resolve('../plugin/bootstrap.js'),'utf8'),context);
  await context.startup({rootURI:'jar:file:///test/bridge.xpi!/'});
  assert.equal(Z.PaperpileDriveBridge.project,runtime.project);
  await Z.PaperpileDriveBridge.importer.run({Z,io:{},plan:{files:[]},state:{mappings:{}},saveState:async()=>{},exportPDFs:false});
  assert.deepEqual(writes,[runtime.project+'/output/migration/drive-annotation-import-report.json']);
});
