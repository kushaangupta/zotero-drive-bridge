import json
from pathlib import Path
import sys
import unittest

from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from drive_annotation_codec import anchor_rectangles, pdf_rect, convert_comment


class GeometryTests(unittest.TestCase):
    def test_crop_and_rotation(self):
        page = PdfWriter().add_blank_page(width=200, height=400)
        page.cropbox.lower_left = (10, 20)
        page.cropbox.upper_right = (110, 220)
        self.assertEqual(pdf_rect([.1, .2, .3, .4], page), [20., 140., 40., 180.])
        page.rotate(90)
        self.assertEqual(pdf_rect([.1, .2, .3, .4], page), [30., 40., 50., 80.])

    def test_unknown_and_invalid_anchors_are_rejected(self):
        for anchor in ('{}', '[null,null,[[[9,[[0,0,1,1]]]]]]',
                       '[null,null,[[[0,[[0,0,2,1]]]]]]'):
            with self.assertRaises(ValueError):
                anchor_rectangles(anchor, 1)

    def test_existing_export_matches_by_page_text_and_position(self):
        pdf = PdfWriter()
        pdf.add_blank_page(width=200, height=400)
        comment = {'id':'drive1', 'content':'same', 'anchor':json.dumps([None,None,[[[0,[[.1,.2,.3,.4]]]]]])}
        native = [{'key':'KEY12345','page':0,'comment':'same','rect':[20,240,60,320]}]
        rows = convert_comment(comment,pdf,native)
        self.assertEqual(rows[0]['matched_zotero_key'],'KEY12345')
        native.append({**native[0], 'key':'KEY67890'})
        with self.assertRaises(ValueError):
            convert_comment(comment,pdf,native)


if __name__ == '__main__':
    unittest.main()
