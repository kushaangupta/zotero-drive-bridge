const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup() {
  const root = '/test/paperpile-bridge';
  const data = new Map();
  let exported = 0;
  const annotation = {key: 'ANNO0001', annotationType: 'highlight', annotationText: 'quote',
    annotationComment: 'remote body', annotationColor: '#ffd400', annotationPosition: '{}',
    annotationAuthorName: '', annotationPageLabel: '1', isAnnotation: () => true,
    getTags: () => [], loadAllData: async () => {}};
  const attachment = {id: 4, key: 'ATTACH01', parentID: 3, libraryID: 1,
    isPDFAttachment: () => true, isStoredFileAttachment: () => true,
    getFilePathAsync: async () => '/storage/ATTACH01/file.pdf',
    getAnnotations: () => [annotation], loadAllData: async () => {}};
  const item = {id: 3, isRegularItem: () => true, loadAllData: async () => {},
    getCreators: () => [{creatorTypeID: 1, lastName: 'First'}, {creatorTypeID: 1, lastName: 'Last'}],
    getField: field => field === 'date' ? '2026' : 'Title', getAttachments: () => [4]};
  const mapping = {zotero_key: 'ANNO0001', attachment_key: 'ATTACH01', file_id: 'file1',
    drive_comment_id: 'comment1', drive_body: 'remote body', drive_comment: 'remote body',
    zotero_comment: 'remote body', origin: 'drive', drive_suffix: ''};
  data.set(root+'/.state/drive-zotero-state.json',{mappings:{'file1:comment1:0':mapping}});
  data.set(root+'/.state/drive-publication.json',{files:{ATTACH01:{relative_path:'Last/2026-First-Title.pdf'}}});
  data.set(root+'/output/migration/zotero-repair-report.json',{collection_id:1});
  const Z = {Libraries: {userLibraryID: 1}, Collections:{get: () => ({libraryID:1,
    loadAllData:async()=>{},getChildItems:()=>[item]})},
    Items: {getByLibraryAndKey: () => annotation, getAsync: async()=>[attachment]},
    CreatorTypes: {getID:()=>1}, Date:{strToDate:()=>({year:2026})},
    File:{getValidFileName: v=>v}, Attachments:{getFileBaseNameFromItem:()=> '2026-First-Title',
      getStorageDirectory:()=>({path:'/storage/ATTACH01'})},
    PDFWorker:{export: async()=>{exported++;}}, logError:()=>{}};
  const io = {exists:async()=>true,makeDirectory:async()=>{},move:async()=>{},
    stat:async()=>({size:100,lastModified:123})};
  const context = vm.createContext({Zotero:Z,IOUtils:io});
  vm.runInContext(fs.readFileSync(require.resolve('../plugin/engine.js'),'utf8'),context);
  const importer = {run:async()=>({summary:{review:0,errors:0},results:[],preparation_errors:[]})};
  const engine = new context.DriveAnnotationEngine(importer, {project:root});
  engine.read = async(path,fallback)=>data.get(path)??fallback;
  engine.write = async(path,value)=>{data.set(path,JSON.parse(JSON.stringify(value)));};
  engine.desktop = async phase=>{if(phase==='push')data.set(root+'/.state/drive-push-report.json',{conflicts:[],errors:[]});};
  return {engine,data,annotation,attachment,root,get exported(){return exported;}};
}

test('exports to the last-author folder and excludes Drive-born comments from the Drive variant',async()=>{
  const f=setup();
  assert.equal((await f.engine.cycle()).status,'finished');
  const box=f.data.get(f.root+'/.state/drive-outbox.json');
  assert.equal(box.files.ATTACH01.relative_path,'Last/2026-First-Title.pdf');
  assert.deepEqual(box.files.ATTACH01.exclude_keys,['ANNO0001']);
  assert.equal(f.exported,1);
  await f.engine.cycle();
  assert.equal(f.exported,1,'unchanged annotations do not regenerate the PDF');
});
test('a Zotero edit queues a conditional update against the shared Drive comment baseline',async()=>{
  const f=setup();f.annotation.annotationComment='local change';
  await f.engine.cycle();
  const packets=f.data.get(f.root+'/.state/drive-outbox.json').comments;
  assert.equal(packets.length,1);
  assert.equal(packets[0].expected_body,'remote body');
  assert.equal(packets[0].body,'local change');
});
test('linked files are excluded and the bridge refuses an overlapping cycle',async()=>{
  const f=setup();f.attachment.isStoredFileAttachment=()=>false;
  await f.engine.cycle();
  assert.equal(Object.keys(f.data.get(f.root+'/.state/drive-outbox.json').files).length,0);
  f.engine.busy=true;
  assert.equal(await f.engine.cycle(),undefined);
});
