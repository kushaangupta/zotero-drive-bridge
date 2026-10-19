from pathlib import Path
import sys
import unittest

from pypdf import PdfWriter
from pypdf.annotations import Highlight
from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject, DictionaryObject, DecodedStreamObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pdf_sync_compare import compare, document_hash


def document():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=400)
    return writer


def highlight(writer, body):
    annotation = Highlight(rect=[10,20,100,30], quad_points=ArrayObject([
        FloatObject(x) for x in [10,30,100,30,10,20,100,20]]))
    annotation[NameObject('/Contents')] = TextStringObject(body)
    writer.add_annotation(0, annotation)


class ComparisonTests(unittest.TestCase):
    def test_annotation_changes_do_not_change_document_identity(self):
        local, remote = document(), document()
        highlight(local, 'local')
        highlight(remote, 'remote')
        result = compare(local, remote)
        self.assertTrue(result['same_document'])
        self.assertFalse(result['same_annotations'])
        self.assertEqual(result['remote_extra_annotations'], 1)

    def test_extra_remote_annotations_are_preserved_for_review(self):
        local, remote = document(), document()
        highlight(local, 'same')
        highlight(remote, 'same')
        highlight(remote, 'new in Drive')
        result = compare(local, remote)
        self.assertTrue(result['same_document'])
        self.assertEqual(result['remote_extra_annotations'], 1)

    def test_link_targets_remain_part_of_document_identity(self):
        local, remote = document(), document()
        for writer, url in [(local, 'https://example.org/one'), (remote, 'https://example.org/two')]:
            annotation = DictionaryObject({NameObject('/Type'):NameObject('/Annot'),
                NameObject('/Subtype'):NameObject('/Link'),
                NameObject('/Rect'):ArrayObject([FloatObject(v) for v in [1,2,3,4]]),
                NameObject('/A'):DictionaryObject({NameObject('/S'):NameObject('/URI'),
                                                  NameObject('/URI'):TextStringObject(url)})})
            writer.add_annotation(0, annotation)
        self.assertNotEqual(document_hash(local), document_hash(remote))

    def test_changed_page_stream_is_a_different_document(self):
        local, remote = document(), document()
        for writer, data in [(local,b'BT (original) Tj ET'), (remote,b'BT (changed) Tj ET')]:
            stream = DecodedStreamObject()
            stream.set_data(data)
            writer.pages[0][NameObject('/Contents')] = writer._add_object(stream)
        self.assertFalse(compare(local,remote)['same_document'])


if __name__ == '__main__':
    unittest.main()
