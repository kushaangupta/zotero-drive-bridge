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
  const item = {id: 3, libraryID: 1, isRegularItem: () => true, loadAllData: async () => {},
    getCreators: () => [{creatorTypeID: 1, lastName: 'First'}, {creatorTypeID: 1, lastName: 'Last'}],
    getField: field => field === 'date' ? '2026' : 'Title', getAttachments: () => [4]};
  const mapping = {zotero_key: 'ANNO0001', attachment_key: 'ATTACH01', file_id: 'file1',
    drive_comment_id: 'comment1', drive_body: 'remote body', drive_comment: 'remote body',
    zotero_comment: 'remote body', origin: 'drive', drive_suffix: ''};
  data.set(root+'/.state/drive-zotero-state.json',{mappings:{'file1:comment1:0':mapping}});
  data.set(root+'/.state/drive-publication.json',{files:{ATTACH01:{relative_path:'Last/2026-First-Title.pdf'}}});
  const items = [item];
  const attachments = new Map([[attachment.id, attachment]]);
  const Z = {Libraries: {userLibraryID: 1},
    Collections:{get: () => assert.fail('Drive sync must not depend on a collection')},
    Items: {getByLibraryAndKey: () => annotation, getAsync: async ids=>ids.map(id=>attachments.get(id)),
      getAll: async (libraryID, onlyTopLevel, includeDeleted)=>{
        assert.equal(libraryID,1);assert.equal(onlyTopLevel,true);assert.equal(includeDeleted,false);
        return items;
      }},
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
  engine.desktop = async phase=>{
    if(phase==='push')data.set(root+'/.state/drive-push-report.json',{conflicts:[],errors:[]});
    if(phase==='organize')data.set(root+'/.state/author-folder-plan.json',{
      assignments:{ATTACH01:{folder:'F. Last'}},review:[],applied:{errors:[]}});
  };
  return {engine,data,annotation,attachment,item,items,attachments,root,Z,get exported(){return exported;}};
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

test('new stored PDFs use the resolved initial-and-surname folder',async()=>{
  const f=setup();
  f.data.set(f.root+'/.state/drive-publication.json',{files:{}});
  f.Z.PDFWorker.import=async()=>{};
  f.attachment.saveTx=async()=>{};
  assert.equal((await f.engine.cycle()).status,'finished');
  assert.equal(f.data.get(f.root+'/.state/drive-outbox.json').files.ATTACH01.relative_path,
    'F. Last/2026-First-Title.pdf');
  const metadata=f.data.get(f.root+'/.state/author-folder-metadata.json');
  assert.equal(metadata.authors[0].attachment_key,'ATTACH01');
  assert.equal(metadata.authors[0].lastName,'Last');
});

test('unresolved authors preserve registered copies but do not publish unassigned new PDFs',async()=>{
  const f=setup();
  const desktop=f.engine.desktop;
  f.engine.desktop=async phase=>{
    await desktop(phase);
    if(phase==='organize')f.data.set(f.root+'/.state/author-folder-plan.json',{
      assignments:{},review:[{attachment_key:'ATTACH01',reason:'ambiguous_author_name'}],applied:{errors:[]}});
  };
  assert.equal((await f.engine.cycle()).status,'needs_review');
  assert.equal(f.data.get(f.root+'/.state/drive-outbox.json').files.ATTACH01.relative_path,'Last/2026-First-Title.pdf');
  f.data.set(f.root+'/.state/drive-publication.json',{files:{}});
  await f.engine.cycle();
  assert.equal(Object.keys(f.data.get(f.root+'/.state/drive-outbox.json').files).length,0);
});

test('failed reorganization stops before PDF export and push',async()=>{
  const f=setup();
  f.engine.desktop=async phase=>{
    if(phase==='organize')f.data.set(f.root+'/.state/author-folder-plan.json',{
      assignments:{},review:[],applied:{errors:[{error:'changed folder'}]}});
    if(phase==='push')assert.fail('must not push after folder failure');
  };
  assert.equal((await f.engine.cycle()).status,'error');
  assert.equal(f.exported,0);
});

test('papers outside any collection and in nested collections are both exported',async()=>{
  const f=setup();
  f.item.getCollections=()=>[];
  const second={...f.item,id:5,getCollections:()=>[99],getAttachments:()=>[6]};
  const pdf={...f.attachment,id:6,key:'ATTACH02',parentID:5,
    getFilePathAsync:async()=>'/storage/ATTACH02/file.pdf',saveTx:async()=>{}};
  f.items.push(second);f.attachments.set(6,pdf);
  f.Z.Attachments.getStorageDirectory=a=>({path:'/storage/'+a.key});
  f.Z.PDFWorker.import=async()=>{};
  const desktop=f.engine.desktop;
  f.engine.desktop=async phase=>{
    await desktop(phase);
    if(phase==='organize')f.data.get(f.root+'/.state/author-folder-plan.json').assignments.ATTACH02={folder:'F. Last'};
  };
  const status=await f.engine.cycle();
  assert.equal(status.status,'finished');
  assert.equal(status.scope,'my_library');
  assert.equal(status.items_scanned,2);
  assert.deepEqual(Object.keys(f.data.get(f.root+'/.state/drive-outbox.json').files).sort(),['ATTACH01','ATTACH02']);
  assert.equal(f.exported,2);
});

test('multiple collection memberships do not duplicate a paper or require the migration report',async()=>{
  const f=setup();f.item.getCollections=()=>[1,2,3];
  assert.equal((await f.engine.cycle()).status,'finished');
  assert.equal(f.exported,1);
  assert.equal(Object.keys(f.data.get(f.root+'/.state/drive-outbox.json').files).length,1);
});

test('Trash and other libraries are excluded even if returned by the item provider',async()=>{
  const f=setup();
  const unavailable=async()=>assert.fail('excluded items must not be loaded');
  f.items.push({...f.item,id:7,deleted:true,loadAllData:unavailable},
    {...f.item,id:8,libraryID:2,loadAllData:unavailable});
  f.attachments.set(9,{...f.attachment,id:9,key:'TRASH001',deleted:true});
  f.attachments.set(10,{...f.attachment,id:10,key:'GROUP001',libraryID:2});
  f.item.getAttachments=()=>[4,9,10];
  const status=await f.engine.cycle();
  assert.equal(status.items_scanned,1);
  assert.equal(f.exported,1);
  assert.equal(f.data.get(f.root+'/.state/author-folder-metadata.json').authors.length,1);
});

test('standalone PDFs are reported for parent metadata instead of receiving invented author folders',async()=>{
  const f=setup();
  f.items.push({...f.attachment,id:11,key:'SOLO0001',parentID:false,isRegularItem:()=>false});
  const status=await f.engine.cycle();
  assert.equal(status.status,'needs_review');
  assert.equal(status.standalone_pdf_review[0].attachment_key,'SOLO0001');
  assert.equal(f.exported,1);
  assert.equal(f.data.get(f.root+'/.state/author-folder-metadata.json').authors.length,1);
});
