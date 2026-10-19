from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from check_repository import inspect_files, package


class RepositoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='bridge-audit-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_generated_files_are_rejected_even_if_force_added(self):
        errors = inspect_files(['config.local.json', '.state/registry.json', 'paper.pdf'], self.root)
        self.assertEqual(len(errors), 3)

    def test_example_config_is_allowed_but_personal_config_values_are_flagged(self):
        (self.root / 'example.txt').write_text('reader@example.org')
        self.assertEqual(inspect_files(['example.txt'], self.root), [])
        (self.root / 'example.txt').write_text('reader@' + 'personal.test')
        self.assertEqual(inspect_files(['example.txt'], self.root)[0][1], 'non-example email address')

    def test_source_archive_contains_only_explicit_audited_candidates(self):
        (self.root / 'README.md').write_text('Public guide')
        (self.root / '.state').mkdir()
        (self.root / '.state/private.json').write_text('Private state')
        target = package(['README.md'], self.root)
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.namelist(), ['zotero-drive-bridge/README.md'])


if __name__ == '__main__':
    unittest.main()
