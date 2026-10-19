const {test} = require('node:test');
const assert = require('node:assert/strict');
const {decide, run} = require('../scripts/import_drive_annotations.js');

const entry = (body, combined=body) => ({drive_body: body, annotation: {comment: combined}});

test('new Drive comments are created, and existing embedded comments are adopted', () => {
  assert.equal(decide(null, null, entry('new')), 'create');
  assert.equal(decide({comment: 'old'}, null, entry('old')), 'adopt');
});
test('an unchanged Drive comment never overwrites an edit in Zotero', () => {
  assert.equal(decide({comment: 'local edit'}, {drive_comment: 'old', zotero_comment: 'old'}, entry('old')), 'unchanged');
});
test('both sides edited keeps the local comment and flags review', () => {
  assert.equal(decide({comment: 'local edit'}, {drive_comment: 'old', zotero_comment: 'old'}, entry('remote edit')), 'concurrent_edit_preserved');
});
test('Drive changes apply when the Zotero comment still equals the baseline', () => {
  assert.equal(decide({comment: 'old'}, {drive_comment: 'old', zotero_comment: 'old'}, entry('new')), 'update');
});
test('a stale initial export cannot overwrite a later Zotero edit', () => {
  assert.equal(decide({comment: 'new local'}, null, entry('old')), 'concurrent_edit_preserved');
});
test('a deleted mapped annotation is not silently recreated', () => {
  assert.equal(decide(null, {drive_comment: 'old', zotero_comment: 'old'}, entry('new')), 'missing_annotation_needs_review');
});
test('replies append to a matched annotation instead of making a duplicate', () => {
  assert.equal(decide({comment: 'body'}, null, entry('body', 'body\n\nAuthor: reply')), 'update');
});

function fixture() {
  const annotations = new Map();
  const state = {version: 1, mappings: {}};
  const attachment = {id: 10, key: 'ATTACH01', parentID: 20, libraryID: 1,
    isStoredFileAttachment: () => true, isPDFAttachment: () => true,
    loadAllData: async () => {}};
  let serial = 0;
  const Z = {Libraries: {userLibraryID: 1},
    Items: {getAsync: async () => attachment,
      getByLibraryAndKey: (_, key) => annotations.get(key)},
    DataObjectUtilities: {generateKey: () => 'KEY0000' + (++serial)},
    Annotations: {saveFromJSON: async (_, data) => {
      const item = {key: data.key, parentID: 10, annotationComment: data.comment,
        annotationIsExternal: false, isAnnotation: () => true,
        loadAllData: async () => {}, saveTx: async () => {}};
      annotations.set(item.key, item);
      return item;
    }}, File: {putContentsAsync: async () => {}}};
  const item = {...entry('from Drive'), page_index: 0, drive_comment_id: 'drive1'};
  const plan = {files: [{file_id: 'file1', attachment_id: 10, attachment_key: 'ATTACH01',
    zotero_item_id: 20, entries: [item]}]};
  const options = {Z, io: {}, plan, state, saveState: async () => {}, exportPDFs: false};
  return {options, annotations, state, item};
}

test('repeated imports create exactly one native annotation', async () => {
  const f = fixture();
  assert.equal((await run(f.options)).summary.created, 1);
  assert.equal((await run(f.options)).summary.unchanged, 1);
  assert.equal(f.annotations.size, 1);
});

test('a poll with local edits retains the shared baseline for later conflict checks', async () => {
  const f = fixture();
  await run(f.options);
  const annotation = [...f.annotations.values()][0];
  annotation.annotationComment = 'local change';
  await run(f.options);
  assert.equal(f.state.mappings['file1:drive1:0'].zotero_comment, 'from Drive');
  f.item.annotation.comment = 'remote change';
  assert.equal((await run(f.options)).summary.review, 1);
  assert.equal(annotation.annotationComment, 'local change');
});

test('an interrupted create adopts its reserved key after the database commit', async () => {
  const f = fixture();
  await run(f.options);
  const mapping = f.state.mappings['file1:drive1:0'];
  f.state.mappings['file1:drive1:0'] = {zotero_key: mapping.zotero_key, pending: true};
  assert.equal((await run(f.options)).summary.adopted, 1);
  assert.equal(f.annotations.size, 1);
});
