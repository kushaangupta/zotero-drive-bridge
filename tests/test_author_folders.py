import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from author_folders import plan_authors
from organize_author_folders import apply_plan, FOLDER


def author(key, first, last="Smith", **extra):
    return {"attachment_key": key, "firstName": first, "lastName": last, **extra}


class AuthorIdentityTests(unittest.TestCase):
    def test_initials_full_names_and_optional_middle_names_share_one_folder(self):
        plan = plan_authors([author("a", "G.", "Buzsáki"), author("b", "György", "Buzsaki"),
                             author("c", "György R.", "Buzsáki")])
        self.assertEqual(len(plan["state"]["authors"]), 1)
        self.assertEqual({r["folder"] for r in plan["assignments"].values()}, {"G. Buzsaki"})
        self.assertEqual(plan["review"], [])

    def test_different_full_first_names_do_not_merge_even_with_same_initial(self):
        plan = plan_authors([author("a", "John"), author("b", "Jane"), author("c", "J.")])
        self.assertNotEqual(plan["assignments"]["a"]["folder"], plan["assignments"]["b"]["folder"])
        self.assertNotIn("c", plan["assignments"])
        self.assertEqual(plan["review"][0]["reason"], "ambiguous_author_name")

    def test_saved_folder_survives_enriched_metadata_and_later_initial_only_items(self):
        first = plan_authors([author("a", "I.", "Fiete")])
        later = plan_authors([author("a", "Ila R", "Fiete"), author("b", "Ila", "Fiete"),
                              author("c", "I", "Fiete")], first["state"])
        self.assertEqual({a["folder"] for a in later["assignments"].values()}, {"I. Fiete"})
        self.assertEqual(len(later["state"]["authors"]), 1)
        self.assertEqual(next(iter(later["state"]["authors"].values()))["first"], "ila")

    def test_new_namesake_does_not_take_existing_author_folder(self):
        first = plan_authors([author("a", "John")])
        later = plan_authors([author("a", "John"), author("b", "Jane"), author("c", "J")], first["state"])
        self.assertEqual(later["assignments"]["a"]["folder"], "J. Smith")
        self.assertEqual(later["assignments"]["b"]["folder"], "J. Smith (Jane)")
        self.assertNotIn("c", later["assignments"])

    def test_previously_initial_only_identity_needs_review_when_two_full_names_arrive(self):
        first = plan_authors([author("a", "J.")])
        later = plan_authors([author("a", "J."), author("b", "John"), author("c", "Jane")], first["state"])
        self.assertNotIn("a", later["assignments"])
        self.assertEqual(later["review"][0]["reason"], "ambiguous_author_name")

    def test_changed_last_author_and_missing_names_require_review(self):
        first = plan_authors([author("a", "John")])
        plan = plan_authors([author("a", "Jane"), author("b", "", ""), author("c", "")], first["state"])
        self.assertNotIn("a", plan["assignments"])
        self.assertNotIn("b", plan["assignments"])
        self.assertNotIn("c", plan["assignments"])
        self.assertIn("author_changed_requires_review", [r["reason"] for r in plan["review"]])

    def test_surname_only_cannot_choose_between_multiple_people(self):
        plan = plan_authors([author("a", "Edvard", "Moser"), author("b", "May-Britt", "Moser"),
                             author("c", "", "Moser")])
        self.assertEqual(plan["assignments"]["a"]["folder"], "E. Moser")
        self.assertEqual(plan["assignments"]["b"]["folder"], "M. Moser")
        self.assertNotIn("c", plan["assignments"])

    def test_conflicting_middle_names_need_review_but_omitted_middle_names_do_not_split(self):
        plan = plan_authors([author("a", "John Robert"), author("b", "John R."),
                             author("c", "John"), author("d", "John Samuel")])
        self.assertEqual({plan["assignments"][key]["folder"] for key in ("a", "b", "c")}, {"J. Smith"})
        self.assertNotIn("d", plan["assignments"])
        self.assertEqual(plan["review"][0]["reason"], "conflicting_given_names")

    def test_organizations_keep_their_name_and_paths_remain_single_components(self):
        plan = plan_authors([author("a", "", "Example / Consortium", fieldMode=1)])
        self.assertEqual(plan["assignments"]["a"]["folder"], "Example _ Consortium")
        with self.assertRaisesRegex(ValueError, "Duplicate attachment"):
            plan_authors([author("a", "John"), author("a", "John")])


class FakeDrive:
    def __init__(self):
        self.rows = {}
        self.events = []

    def add(self, identity, title, parent="root", folder=False):
        self.rows[identity] = {"id": identity, "title": title, "mimeType": FOLDER if folder else "application/pdf",
                               "parents": [{"id": parent}], "etag": "etag", "md5Checksum": "same-bytes"}
        return self.rows[identity]

    def children(self, parent):
        return [copy.deepcopy(row) for row in self.rows.values()
                if parent in [r["id"] for r in row["parents"]] and not row.get("trashed")]

    def metadata(self, identity):
        return copy.deepcopy(self.rows[identity])

    def rename(self, row, title):
        self.events.append(("rename", row["id"]))
        self.rows[row["id"]]["title"] = title
        return self.metadata(row["id"])

    def create(self, root, title):
        identity = "new" + str(len(self.rows))
        self.events.append(("create", identity))
        return copy.deepcopy(self.add(identity, title, root, folder=True))

    def move(self, row, old, new):
        self.events.append(("move", row["id"]))
        self.rows[row["id"]]["parents"] = [{"id": new}]
        return self.metadata(row["id"])

    def trash(self, row):
        self.events.append(("trash", row["id"]))
        self.rows[row["id"]]["trashed"] = True


class FolderMoveTests(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDrive()
        self.drive.add("old", "Smith", folder=True)
        self.drive.add("pdf1", "paper.pdf", "old")
        self.destination = "google-drive://reader@example.org/account/root"
        self.registry = {"destination": self.destination, "files": {"a": {
            "remote_uri": self.destination + "/old/pdf1", "relative_path": "Smith/paper.pdf"}}}
        self.plan = {"assignments": {"a": {"author_id": "john", "folder": "J. Smith"}}}
        self.persisted = []

    def apply(self):
        return apply_plan(self.registry, self.plan, self.drive,
                          lambda value: self.persisted.append(copy.deepcopy(value)))

    def test_single_author_renames_folder_without_touching_pdf_id_or_bytes(self):
        result = self.apply()
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.drive.events, [("rename", "old")])
        self.assertEqual(self.registry["files"]["a"]["remote_uri"], self.destination + "/old/pdf1")
        self.assertEqual(self.registry["files"]["a"]["relative_path"], "J. Smith/paper.pdf")
        self.assertEqual(self.drive.rows["pdf1"]["md5Checksum"], "same-bytes")

    def test_mixed_folder_moves_original_ids_into_separate_author_folders(self):
        self.drive.add("pdf2", "other.pdf", "old")
        self.registry["files"]["b"] = {"remote_uri": self.destination + "/old/pdf2", "relative_path": "Smith/other.pdf"}
        self.plan["assignments"]["b"] = {"author_id": "peter", "folder": "P. Smith"}
        result = self.apply()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["files_moved"], 2)
        self.assertEqual(result["empty_folders_trashed"], 1)
        self.assertEqual(self.registry["files"]["b"]["remote_uri"].rsplit("/", 1)[-1], "pdf2")

    def test_unknown_files_in_source_folder_are_left_in_place(self):
        self.drive.add("unowned", "personal.pdf", "old")
        result = self.apply()
        self.assertEqual(result["files_moved"], 1)
        self.assertEqual(self.drive.rows["old"]["title"], "Smith")
        self.assertEqual(self.drive.rows["unowned"]["parents"], [{"id": "old"}])
        self.assertNotIn(("trash", "old"), self.drive.events)

    def test_existing_different_pdf_with_same_name_blocks_move(self):
        self.drive.add("target", "J. Smith", folder=True)
        self.drive.add("conflict", "paper.pdf", "target")
        result = self.apply()
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(self.drive.rows["pdf1"]["parents"], [{"id": "old"}])
        self.assertEqual(self.persisted, [])

    def test_retry_after_move_before_checkpoint_recovers_by_file_id(self):
        self.drive.add("target", "J. Smith", folder=True)
        self.drive.rows["pdf1"]["parents"] = [{"id": "target"}]
        result = self.apply()
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.registry["files"]["a"]["remote_uri"], self.destination + "/target/pdf1")
        self.assertFalse(any(event[0] == "move" for event in self.drive.events))


if __name__ == "__main__":
    unittest.main()
