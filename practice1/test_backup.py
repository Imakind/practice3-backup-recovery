"""Дополнительные проверки. Все изменения — в tempfile, не в учебных данных."""
import contextlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {"ROOT": self.root, "SOURCE": self.root / "company_data",
                            "BACKUP_DIR": self.root / "backups",
                            "RESTORE_TEST": self.root / "restore_test",
                            "KEEP_LAST": 5}.items():
            self.stack.enter_context(patch.object(backup, name, value))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        (backup.SOURCE / "buh").mkdir(parents=True)
        (backup.SOURCE / "buh" / "one.csv").write_text("a;1\nb;2\n", encoding="utf-8")
        (backup.SOURCE / "buh" / "two.csv").write_text("c;3\n", encoding="utf-8")
        self.before = backup.make_manifest(backup.SOURCE)

    def test_good_backup_and_restore(self):
        archive = backup.create_backup()
        self.assertTrue(backup.verify_restore(archive))
        (backup.SOURCE / "buh" / "one.csv").unlink()
        self.assertGreaterEqual(backup.restore(archive), 0)
        self.assertEqual(backup.make_manifest(backup.SOURCE), self.before)

    def test_rotation_keeps_five_pairs_and_identical_file_hashes(self):
        archives = []
        for _ in range(8):
            archives.append(backup.create_backup())
            backup.rotate()
        remaining = sorted(backup.BACKUP_DIR.glob("*.tar.gz"))
        self.assertEqual(remaining, archives[-5:])
        self.assertEqual(len(list(backup.BACKUP_DIR.glob("*.json"))), 5)
        for archive in remaining:
            manifest = json.loads(backup.manifest_path(archive).read_text())
            self.assertEqual(manifest["files"], self.before)

    def test_tampered_manifest_detects_changed_and_extra(self):
        archive = backup.create_backup()
        path = backup.manifest_path(archive)
        manifest = json.loads(path.read_text())
        names = sorted(manifest["files"])
        manifest["files"][names[0]] = "0" * 64
        del manifest["files"][names[1]]
        path.write_text(json.dumps(manifest))
        differences = backup.compare_manifests(manifest["files"], self.before)
        self.assertEqual(differences, {"missing": [], "extra": [names[1]],
                                       "changed": [names[0]]})
        self.assertFalse(backup.verify_restore(archive))
        with self.assertRaises(ValueError):
            backup.restore(archive)
        self.assertEqual(backup.make_manifest(backup.SOURCE), self.before)

    def test_missing_extra_changed_all_reported(self):
        differences = backup.compare_manifests({"a": "1", "b": "2"},
                                               {"b": "3", "c": "4"})
        self.assertEqual(differences, {"missing": ["a"], "extra": ["c"],
                                       "changed": ["b"]})

    def test_corrupt_archive_does_not_replace_source(self):
        archive = backup.create_backup()
        with archive.open("r+b") as stream:
            stream.seek(10)
            stream.write(b"BAD")
        self.assertFalse(backup.verify_restore(archive))
        with self.assertRaises(ValueError):
            backup.restore(archive)
        self.assertEqual(backup.make_manifest(backup.SOURCE), self.before)

    def test_missing_manifest_fails_without_replacing_source(self):
        archive = backup.create_backup()
        backup.manifest_path(archive).unlink()
        self.assertFalse(backup.verify_restore(archive))
        with self.assertRaises(FileNotFoundError):
            backup.restore(archive)
        self.assertEqual(backup.make_manifest(backup.SOURCE), self.before)

    def test_unsafe_archive_is_rejected_even_with_matching_hash(self):
        archive = backup.create_backup()
        with tarfile.open(archive, "w:gz") as tar:
            item = tarfile.TarInfo("../outside.txt")
            item.size = 4
            tar.addfile(item, io.BytesIO(b"oops"))
        path = backup.manifest_path(archive)
        manifest = json.loads(path.read_text())
        manifest["archive_sha256"] = backup.sha256(archive)
        path.write_text(json.dumps(manifest))
        self.assertFalse(backup.verify_restore(archive))
        self.assertFalse((self.root / "outside.txt").exists())
        with self.assertRaises(ValueError):
            backup.restore(archive)
        self.assertEqual(backup.make_manifest(backup.SOURCE), self.before)

    def test_symlink_source_file_is_rejected(self):
        (backup.SOURCE / "link").symlink_to(backup.SOURCE / "buh" / "one.csv")
        with self.assertRaises(ValueError):
            backup.create_backup()

    def test_invalid_rotation_count_does_not_delete_backups(self):
        archive = backup.create_backup()
        with patch.object(backup, "KEEP_LAST", 0):
            with self.assertRaises(ValueError):
                backup.rotate()
        self.assertTrue(archive.exists())

    def test_multiple_fast_backups_do_not_overwrite(self):
        archives = [backup.create_backup() for _ in range(3)]
        self.assertEqual(len(set(archives)), 3)
        self.assertTrue(all(path.exists() for path in archives))


if __name__ == "__main__":
    unittest.main(verbosity=2)
