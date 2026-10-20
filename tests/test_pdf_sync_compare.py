from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zlib

from pypdf import PdfWriter
from pypdf.annotations import Highlight
from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject, DictionaryObject, DecodedStreamObject, EncodedStreamObject

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

    def compressed_document(self, content, level=6):
        writer = document()
        stream = EncodedStreamObject()
        stream._data = zlib.compress(content, level)
        stream[NameObject('/Filter')] = NameObject('/FlateDecode')
        writer.pages[0][NameObject('/Contents')] = writer._add_object(stream)
        return writer

    def test_oversized_streams_compare_without_disabling_the_decompression_limit(self):
        local = self.compressed_document(b'original' * 1000)
        remote = self.compressed_document(b'original' * 1000)
        changed = self.compressed_document(b'changed!' * 1000)
        with patch('pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH', 32):
            self.assertTrue(compare(local, remote)['same_document'])
            self.assertFalse(compare(local, changed)['same_document'])

    def test_differently_encoded_oversized_streams_require_review(self):
        local = self.compressed_document(b'original' * 1000, level=1)
        remote = self.compressed_document(b'original' * 1000, level=9)
        with patch('pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH', 32):
            self.assertFalse(compare(local, remote)['same_document'])

    def test_identical_encoded_resources_do_not_need_decompression(self):
        local = self.compressed_document(b'original' * 1000)
        remote = self.compressed_document(b'original' * 1000)
        with patch.object(EncodedStreamObject, 'get_data', side_effect=AssertionError('unnecessary decode')):
            self.assertTrue(compare(local, remote)['same_document'])

    def test_different_compression_of_normal_streams_still_matches(self):
        local = self.compressed_document(b'original' * 1000, level=1)
        remote = self.compressed_document(b'original' * 1000, level=9)
        self.assertTrue(compare(local, remote)['same_document'])


if __name__ == '__main__':
    unittest.main()
