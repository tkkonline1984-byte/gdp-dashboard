import tempfile
import unittest
from pathlib import Path

from storage import (
    GitHubSubmissionStore,
    LocalSubmissionStore,
    StorageConfigurationError,
    StorageError,
    submission_paths,
)


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeGitHubSession:
    def __init__(self, private=True):
        self.private = private
        self.blob_count = 0
        self.tree_payload = None

    def request(self, method, url, headers=None, json=None, timeout=None):
        path = url.split("/repos/owner/repo", 1)[-1]
        if method == "GET" and path == "":
            return FakeResponse(200, {"private": self.private, "permissions": {"push": True}})
        if method == "POST" and path == "/git/blobs":
            self.blob_count += 1
            return FakeResponse(201, {"sha": f"blob-{self.blob_count}"})
        if method == "GET" and path == "/git/ref/heads/main":
            return FakeResponse(200, {"object": {"sha": "parent-sha"}})
        if method == "GET" and path == "/git/commits/parent-sha":
            return FakeResponse(200, {"tree": {"sha": "base-tree"}})
        if method == "POST" and path == "/git/trees":
            self.tree_payload = json
            return FakeResponse(201, {"sha": "new-tree"})
        if method == "POST" and path == "/git/commits":
            return FakeResponse(201, {"sha": "new-commit"})
        raise AssertionError(f"Unexpected request: {method} {path}")

    def patch(self, url, headers=None, json=None, timeout=None):
        return FakeResponse(200, {"object": {"sha": "new-commit"}})


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = LocalSubmissionStore(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_submission_paths_are_deterministic(self):
        image, metadata = submission_paths(
            "submissions", "8850127000016", "2026-09-01T10:20:30+07:00", "abc-123"
        )
        self.assertEqual(image, "submissions/2026/09/01/8850127000016/abc-123.jpg")
        self.assertEqual(metadata, "submissions/2026/09/01/8850127000016/abc-123.json")

    def test_save_list_and_read(self):
        metadata = {
            "submission_id": "test-001",
            "submitted_at": "2026-09-01T10:20:30+07:00",
            "barcode": "8850127000016",
            "employee_name": "Tester",
        }
        result = self.store.save_submission(metadata, b"jpeg-data")
        records = self.store.list_submissions()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["barcode"], "8850127000016")
        self.assertEqual(self.store.get_file_bytes(result.image_path), b"jpeg-data")

    def test_prevents_path_traversal(self):
        with self.assertRaises(StorageError):
            self.store.get_file_bytes("../../outside.txt")

    def test_github_commit_contains_image_and_metadata(self):
        session = FakeGitHubSession(private=True)
        store = GitHubSubmissionStore(
            token="test-token", repository="owner/repo", branch="main", session=session
        )
        metadata = {
            "submission_id": "test-001",
            "submitted_at": "2026-09-01T10:20:30+07:00",
            "barcode": "8850127000016",
        }
        result = store.save_submission(metadata, b"jpeg-data")
        self.assertEqual(result.commit_sha, "new-commit")
        self.assertEqual(session.blob_count, 2)
        paths = [item["path"] for item in session.tree_payload["tree"]]
        self.assertEqual(len(paths), 2)
        self.assertTrue(any(path.endswith(".jpg") for path in paths))
        self.assertTrue(any(path.endswith(".json") for path in paths))

    def test_github_refuses_public_repository(self):
        store = GitHubSubmissionStore(
            token="test-token",
            repository="owner/repo",
            session=FakeGitHubSession(private=False),
        )
        with self.assertRaises(StorageConfigurationError):
            store.validate_connection()


if __name__ == "__main__":
    unittest.main()
