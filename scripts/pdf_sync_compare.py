#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compare rendered PDF document inputs separately from markup annotations.

Google Drive can regenerate a PDF with its comments embedded. Such a rewrite
changes checksums and object numbers without changing the paper. Resolve objects
and decoded streams so object renumbering/compression do not look like content
edits. Links and form widgets remain part of the document comparison.
"""
import hashlib
import json
import sys

from pypdf import PdfReader
from pypdf.errors import LimitReachedError
from pypdf.generic import IndirectObject, StreamObject, DictionaryObject, ArrayObject, NameObject

from drive_annotation_codec import text, overlap

MARKUP = {'/Highlight','/Underline','/StrikeOut','/Squiggly','/Text','/FreeText',
          '/Ink','/Square','/Circle','/Polygon','/PolyLine','/Caret','/Stamp','/Popup'}


def document_hash(pdf, decode_streams=True):
    cache, active = {}, set()

    def digest(value):
        if isinstance(value, IndirectObject):
            value = value.get_object()
        if isinstance(value, (DictionaryObject, ArrayObject)):
            identity = id(value)
            if identity in active:
                return b'cycle'
            if identity in cache:
                return cache[identity][1]
            active.add(identity)
            h = hashlib.sha256()
            if isinstance(value, DictionaryObject):
                skip = {'/Parent','/P'}
                if isinstance(value, StreamObject):
                    skip |= {'/Length','/DL'}
                    if not decode_streams:
                        data = value._data
                        h.update(b'encoded-stream\0')
                    else:
                        try:
                            data = value.get_data()
                        except LimitReachedError:
                            # Keep pypdf's decompression limit. Oversized streams
                            # use encoded bytes plus filter parameters; a changed
                            # encoding conservatively requires review.
                            data = value._data
                            if not isinstance(data, bytes):
                                raise
                            h.update(b'encoded-stream\0')
                        else:
                            skip |= {'/Filter','/DecodeParms'}
                            h.update(b'decoded-stream\0')
                    h.update(hashlib.sha256(data).digest())
                for key in sorted(value):
                    if key not in skip:
                        h.update(str(key).encode())
                        h.update(digest(value.raw_get(key)))
            else:
                for child in value:
                    h.update(digest(child))
            active.remove(identity)
            cache[identity] = (value, h.digest())
            return cache[identity][1]
        if isinstance(value, bytes):
            return hashlib.sha256(value).digest()
        # Float rounding during Drive's PDF rewrite is insignificant at this
        # precision, while text, decoded streams and link destinations stay exact.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return format(float(value), '.7g').encode()
        return (type(value).__name__ + ':' + str(value)).encode()

    result = hashlib.sha256()
    for page in pdf.pages:
        content = DictionaryObject({key: value for key, value in page.items() if key not in ('/Annots','/Parent')})
        nonmarkup = ArrayObject([a for a in page.get('/Annots', []) if a.get_object().get('/Subtype') not in MARKUP])
        content[NameObject('/Annots')] = nonmarkup
        result.update(digest(content))
    return result.hexdigest()


def annotations(pdf):
    result = []
    for page, obj in enumerate(pdf.pages):
        for ref in obj.get('/Annots', []):
            a = ref.get_object()
            subtype = a.get('/Subtype')
            if subtype not in MARKUP or subtype == '/Popup':
                continue
            rect = a.get('/Rect')
            if not rect or len(rect) != 4:
                raise ValueError('A PDF markup annotation has no valid rectangle')
            geometry = json.dumps(a.get('/InkList') or a.get('/Vertices') or [], default=str)
            result.append({'page':page, 'type':subtype, 'comment':text(a.get('/Contents')),
                           'rect':[float(v) for v in rect], 'geometry':geometry})
    return result


def compare(local, remote):
    native, remaining = annotations(local), annotations(remote)
    matched = set()
    extras = 0
    for annotation in remaining:
        candidates = [(i,a) for i,a in enumerate(native) if i not in matched
                      and a['page'] == annotation['page'] and a['type'] == annotation['type']
                      and a['comment'] == annotation['comment'] and a['geometry'] == annotation['geometry']
                      and overlap(a['rect'],annotation['rect']) > .7]
        if candidates:
            index, _ = max(candidates,key=lambda pair:overlap(pair[1]['rect'],annotation['rect']))
            matched.add(index)
        else:
            extras += 1
    # Annotation exports normally preserve compressed page/image streams. This
    # exact encoded comparison avoids expanding large resources unnecessarily.
    same_document = (document_hash(local, decode_streams=False) == document_hash(remote, decode_streams=False)
                     or document_hash(local) == document_hash(remote))
    return {'same_document':same_document,
            'same_annotations':extras==0 and len(matched)==len(native),
            'remote_extra_annotations':extras, 'local_additions':len(native)-len(matched)}


if __name__ == '__main__':
    print(json.dumps(compare(PdfReader(sys.argv[1]), PdfReader(sys.argv[2]))))
