from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from bridge_config import load_config, state_path, runtime_config, drive_identity, configured_path
from build_drive_bridge import build
from prepare_import import prepare


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='bridge-setup-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def configure(self, data):
        (self.root / 'config.local.json').write_text(json.dumps(data))

    def test_missing_required_config_has_an_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'setup.py init'):
            load_config(self.root, required=True)

    def test_default_state_and_local_override_stay_in_private_directory(self):
        config = load_config(self.root)
        self.assertEqual(state_path('registry', config, self.root), self.root / '.state/drive-publication.json')
        self.configure({'state_files': {'registry': '.state/existing-registry.json'}})
        config = load_config(self.root)
        self.assertEqual(state_path('registry', config, self.root), self.root / '.state/existing-registry.json')

    def test_state_traversal_is_rejected(self):
        self.configure({'state_files': {'registry': '../private.json'}})
        with self.assertRaisesRegex(ValueError, 'inside .state'):
            load_config(self.root)

    def test_source_and_destination_must_be_different(self):
        uri = 'google-drive://reader@example.org/root/folder'
        self.configure({'source_drive_uri':uri,'destination_drive_uri':uri+'/'})
        with self.assertRaisesRegex(ValueError, 'separate source and destination'):
            load_config(self.root)

    def test_unknown_keys_do_not_silently_accept_credentials(self):
        self.configure({'password': 'synthetic-test-value'})
        with self.assertRaisesRegex(ValueError, 'Unknown configuration keys'):
            load_config(self.root)

    def test_invalid_poll_intervals_are_rejected(self):
        for minutes in (0, True, 2.5, 1441):
            self.configure({'poll_minutes': minutes})
            with self.assertRaisesRegex(ValueError, 'poll_minutes'):
                load_config(self.root)

    def test_drive_identity_handles_encoded_at_sign(self):
        self.assertEqual(drive_identity('google-drive://reader%40example.org/root/folder'), 'reader@example.org')
        for uri in ('https://example.org/root/folder', 'google-drive://reader@example.org/root/../folder'):
            with self.assertRaises(ValueError):
                drive_identity(uri)

    def test_virtual_environment_interpreter_path_is_not_dereferenced(self):
        executable = self.root / '.venv/bin/python'
        executable.parent.mkdir(parents=True)
        executable.symlink_to(sys.executable)
        self.assertEqual(configured_path('pdf_python', load_config(self.root), self.root), executable)
        self.assertNotEqual(executable, executable.resolve())

    def test_portable_xpi_excludes_local_config_and_generated_commands(self):
        for name in ('plugin', 'scripts'):
            shutil.copytree(ROOT / name, self.root / name, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT / 'LICENSE', self.root / 'LICENSE')
        self.configure({'destination_drive_uri': 'google-drive://reader@example.org/root/folder'})
        package, commands = build(self.root)
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(set(archive.namelist()), {'manifest.json', 'bootstrap.js', 'engine.js', 'import_drive_annotations.js', 'LICENSE'})
            for name in archive.namelist():
                self.assertNotIn(str(self.root), archive.read(name).decode())
                self.assertNotIn('reader@example.org', archive.read(name).decode())
        self.assertIn(str(self.root / 'output/setup/preview_repair.js'), commands)
        installer = (self.root / 'output/setup/install_drive_bridge.js').read_text()
        self.assertNotIn('__PAPERPILE_PROJECT_ROOT__', installer)
        self.assertIn('extensions.paperpileDriveBridge.projectPath', installer)
        runtime = json.loads((self.root / '.state/runtime-config.json').read_text())
        self.assertEqual(runtime['project'], str(self.root))

    def test_prepared_ris_keeps_count_and_manifest_uses_collection_name(self):
        ris, export, index = (self.root / name for name in ('input.ris', 'input.json', 'index.json'))
        ris.write_text('TY  - JOUR\nTI  - Synthetic Paper\nL1  - allPapers/Author/paper.pdf\nER  - \n')
        export.write_text(json.dumps([{'_id':'paper1','title':'Synthetic Paper','attachments':[{'filename':'allPapers/Author/paper.pdf'}]}]))
        index.write_text(json.dumps({'resolved':[{'relative_path':'allPapers/Author/paper.pdf','local_path':'/test/mount/file-id','size':123,'expected_size':123}]}))
        output = self.root / 'output'
        prepare(ris, export, index, output, 'My Papers')
        self.assertIn('L1  - /test/mount/file-id', (output / 'My Papers.ris').read_text())
        manifest = json.loads((output / 'migration-manifest.json').read_text())
        self.assertEqual(manifest['collection_name'], 'My Papers')
        self.assertEqual(len(manifest['references']), 1)


if __name__ == '__main__':
    unittest.main()
