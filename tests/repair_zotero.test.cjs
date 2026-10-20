const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {runRepair, buildPlan} = require('../scripts/repair_zotero.js');
const AUTO_MOVE = 'extensions.zotmoov.enable_automove';

function parent(id, doi, title) {
  return {id, key: 'ITEM' + id, children: [], libraryID: 1,
    getField: name => ({DOI: doi, title}[name] || ''),
    getAttachments() { return this.children; },
    isRegularItem: () => true, loadAllData: async () => {}};
}

async function fixture(t, options = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'paperpile-repair-test-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const source = path.join(root, 'drive', 'opaque-drive-id');
  await fs.mkdir(path.dirname(source));
  await fs.writeFile(source, 'Original PDF bytes and embedded annotations');
  const item = parent(1, '10.123/example', 'Test paper');
  const record = {paperpile_id: 'paperpile1', doi: 'https://doi.org/10.123/EXAMPLE',
    title: 'Test paper', authors: [{last: 'Smith'}, {last: 'Garcia'}], published: {year: '2024'},
    attachments: [{filename: 'allPapers/Garcia/paper.pdf', mounted: {local_path: source}}]};
  const manifest = {collection_name: 'Paperpile export', references: [record]};
  const attachments = new Map();
  const preferences = new Map([[AUTO_MOVE, true]]);
  const calls = {imports: 0, annotationImports: 0};
  let failAnnotations = Boolean(options.failAnnotations);
  const io = {
    makeDirectory: p => fs.mkdir(p, {recursive: true}),
    exists: async p => { try { await fs.stat(p); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; } },
    move: (from, to) => fs.rename(from, to),
  };
  const Z = {
    Libraries: {userLibraryID: 1, get: () => ({editable: true, filesEditable: true})},
    Collections: {getByLibrary: () => [{id: 1, libraryID: 1, name: 'Paperpile export',
      loadAllData: async () => {}, getChildItems: () => [item]}]},
    Items: {getAsync: async ids => ids.map(id => attachments.get(id))},
    Prefs: {get: key => preferences.get(key), set: (key, value) => preferences.set(key, value),
      clear: key => preferences.delete(key)},
    File: {getContentsAsync: p => fs.readFile(p, 'utf8'), putContentsAsync: (p, data) => fs.writeFile(p, data),
      getValidFileName: value => value.replace(/[\\/:]/g, '')},
    Attachments: {
      getStorageDirectory: a => ({path: a.storage}),
      getFileBaseNameFromItem: (_item, opts) => {
        assert.match(opts.formatString, /title case="pascal"/);
        return '2024-Smith-TestPaper';
      },
      importFromFile: async opts => {
        assert.equal(preferences.get(AUTO_MOVE), false, 'ZotMoov must be paused during attachment creation');
        assert.equal(opts.moveFile, false, 'the original must be copied, never moved');
        assert.equal(opts.contentType, 'application/pdf', 'Drive IDs do not have file extensions');
        calls.imports++;
        const id = 100 + calls.imports;
        const storage = path.join(root, 'zotero', String(id));
        await fs.mkdir(storage, {recursive: true});
        const a = {id, key: 'ATT' + id, storage, filePath: path.join(storage, 'opaque-id'), annotations: [],
          isPDFAttachment: () => true, isStoredFileAttachment: () => true,
          getFilePathAsync() { return Promise.resolve(this.filePath); },
          async renameAttachmentFile(name) {
            const next = path.join(this.storage, name);
            if (next !== this.filePath) await fs.rename(this.filePath, next);
            this.filePath = next;
            return true;
          }, saveTx: async () => {}, loadAllData: async () => {},
          getAnnotations() { return this.annotations; }};
        await fs.copyFile(opts.file, a.filePath);
        attachments.set(id, a);
        item.children.push(id);
        return a;
      },
    },
    PDFWorker: {
      import: async (id, priority, password, transfer) => {
        assert.equal(transfer, true);
        calls.annotationImports++;
        if (failAnnotations) throw new Error('simulated annotation failure');
        const a = attachments.get(id);
        assert.equal(a.isStoredFileAttachment(), true);
        assert.notEqual(a.filePath, source);
        a.annotations = [{annotationIsExternal: false}, {annotationIsExternal: false}];
        await fs.writeFile(a.filePath, 'Zotero copy with annotations transferred to the database');
      },
      export: async (id, destination) => {
        const a = attachments.get(id);
        await fs.writeFile(destination, 'Export with ' + a.annotations.length + ' embedded annotations');
      },
    },
  };
  return {root, source, item, record, manifest, Z, io, calls, attachments, preferences,
    setFailAnnotations: value => { failAnnotations = value; },
    run: extra => runRepair({Z, io, manifest, directory: path.join(root, 'output'), ...extra})};
}

test('ambiguous DOI and conflicting DOI/title combinations require review', () => {
  const first = parent(1, '10.1/a', 'Same title');
  const duplicate = parent(2, '10.1/a', 'Other title');
  const result = buildPlan([{doi: '10.1/a', title: 'Same title'}, {doi: '10.1/b', title: 'Same title'}], [first, duplicate]);
  assert.equal(result[0].reason, 'ambiguous_reference');
  assert.equal(result[1].reason, 'reference_not_found');
  assert.equal(buildPlan([{doi: '', title: ''}], [first])[0].reason, 'reference_not_found');
});

test('copies preserve source bytes, annotations use stored copies, and retries do not duplicate PDFs', async t => {
  const f = await fixture(t);
  const original = await fs.readFile(f.source);
  const first = await f.run();
  assert.equal(first.summary.copied_pdfs, 1);
  assert.equal(first.summary.annotations_in_zotero, 2);
  assert.equal(first.summary.annotated_exports, 1);
  assert.match(first.results[0].export_path, /Garcia\/2024-Smith-TestPaper\.pdf$/);
  assert.deepEqual(await fs.readFile(f.source), original);
  const second = await f.run();
  assert.equal(second.summary.copied_pdfs, 0);
  assert.equal(second.summary.reused_pdfs, 1);
  assert.equal(second.results[0].export_path, first.results[0].export_path);
  assert.equal(f.calls.imports, 1);
  assert.equal(f.preferences.get(AUTO_MOVE), true);
  assert.equal(f.Z.paperpileRepairRunning, false);
});

test('a failed annotation import can resume using the attachment already copied', async t => {
  const f = await fixture(t, {failAnnotations: true});
  const first = await f.run();
  assert.equal(first.summary.errors, 1);
  assert.equal(f.preferences.get(AUTO_MOVE), true);
  f.setFailAnnotations(false);
  const second = await f.run();
  assert.equal(second.summary.errors, 0);
  assert.equal(second.summary.reused_pdfs, 1);
  assert.equal(f.calls.imports, 1);
});

test('the prepared author folder controls the migration export layout', async t => {
  const f = await fixture(t);
  f.record.author_folder = 'G. Garcia';
  const report = await f.run();
  assert.match(report.results[0].export_path, /G\. Garcia\/2024-Smith-TestPaper\.pdf$/);
});

test('an ambiguous prepared author does not fall back to a mixed surname folder', async t => {
  const f = await fixture(t);
  f.record.author_folder = null;
  const report = await f.run();
  assert.equal(report.summary.annotated_exports, 0);
  assert.equal(report.results[0].export_needs_metadata_review, true);
});

test('unresolved PDFs and references without PDFs remain references without fabricated attachments', async t => {
  const f = await fixture(t);
  f.record.attachments[0].mounted = null;
  let report = await f.run();
  assert.equal(report.summary.unresolved_pdfs, 1);
  f.record.attachments = [];
  report = await f.run();
  assert.equal(report.summary.references_without_pdfs, 1);
  assert.equal(f.calls.imports, 0);
});

test('preview does not modify the library or ZotMoov settings', async t => {
  const f = await fixture(t);
  const report = await f.run({dryRun: true});
  assert.equal(report.status, 'preview');
  assert.equal(report.summary.matched_references, 1);
  assert.equal(f.calls.imports, 0);
  assert.equal(f.calls.annotationImports, 0);
  assert.equal(f.preferences.get(AUTO_MOVE), true);
});

test('an existing linked PDF is never transferred or modified in place', async t => {
  const f = await fixture(t);
  f.item.children.push(77);
  f.attachments.set(77, {id: 77, key: 'LINKED', storage: path.join(f.root, 'zotero', '77'),
    isPDFAttachment: () => true, isStoredFileAttachment: () => false,
    getFilePathAsync: async () => f.source});
  const original = await fs.readFile(f.source);
  const report = await f.run();
  assert.equal(report.results[0].status, 'linked_or_nonstandard_pdf_needs_review');
  assert.equal(f.calls.annotationImports, 0);
  assert.deepEqual(await fs.readFile(f.source), original);
});

test('an unowned export with the same name is preserved', async t => {
  const f = await fixture(t);
  const existing = path.join(f.root, 'output', 'annotated-pdfs', 'Garcia', '2024-Smith-TestPaper.pdf');
  await fs.mkdir(path.dirname(existing), {recursive: true});
  await fs.writeFile(existing, 'Existing independent PDF');
  const report = await f.run();
  assert.notEqual(report.results[0].export_path, existing);
  assert.equal(await fs.readFile(existing, 'utf8'), 'Existing independent PDF');
});
