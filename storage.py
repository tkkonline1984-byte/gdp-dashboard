from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from store_map import is_complete_location, locations_differ, normalize_location

try:
    import requests
except ModuleNotFoundError:  # Allows local-only diagnostics before dependencies are installed.
    requests = None  # type: ignore[assignment]


class StorageError(RuntimeError):
    pass


class StorageConfigurationError(StorageError):
    pass


@dataclass(frozen=True)
class SubmissionResult:
    submission_id: str
    image_path: str
    metadata_path: str
    repository: str
    commit_sha: str | None = None
    location_changed: bool = False
    previous_location: dict[str, Any] | None = None
    current_location: dict[str, Any] | None = None


def _safe_segment(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip(".-")
    return cleaned[:100] or fallback


def submission_paths(root: str, barcode: str, submitted_at: str, submission_id: str) -> tuple[str, str]:
    date = submitted_at[:10].replace("-", "/")
    safe_id = _safe_segment(submission_id, "submission")
    safe_barcode = _safe_segment(barcode, "barcode")
    base = "/".join(part.strip("/") for part in (root, date, safe_barcode) if part.strip("/"))
    return f"{base}/{safe_id}.jpg", f"{base}/{safe_id}.json"


def location_index_path(root: str, barcode: str) -> str:
    safe_barcode = _safe_segment(barcode, "barcode")
    base = "/".join(part.strip("/") for part in (root, "_locations") if part.strip("/"))
    return f"{base}/{safe_barcode}.json"


class GitHubSubmissionStore:
    """Atomically save one image and one metadata JSON file in a GitHub commit."""

    api_root = "https://api.github.com"

    def __init__(
        self,
        token: str,
        repository: str,
        branch: str = "main",
        data_root: str = "submissions",
        allow_public_storage: bool = False,
        session: Any | None = None,
    ) -> None:
        self.token = str(token or "").strip()
        self.repository = str(repository or "").strip().strip("/")
        self.branch = str(branch or "main").strip()
        self.data_root = str(data_root or "submissions").strip("/")
        self.allow_public_storage = bool(allow_public_storage)
        if session is None and requests is None:
            raise StorageConfigurationError("ยังไม่ได้ติดตั้ง requests กรุณารันตัวติดตั้งก่อน")
        self.session = session or requests.Session()
        self._repo_private: bool | None = None
        self._validated_info: dict[str, Any] | None = None
        self._validated_at = 0.0
        if not self.token:
            raise StorageConfigurationError("ยังไม่ได้ตั้งค่า GITHUB_TOKEN")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise StorageConfigurationError("GITHUB_REPOSITORY ต้องเป็น owner/repository")
        if not self.branch:
            raise StorageConfigurationError("ยังไม่ได้ตั้งค่า GITHUB_BRANCH")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "TKK-Product-Intake-Hub",
        }

    def _url(self, path: str) -> str:
        return f"{self.api_root}/repos/{self.repository}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        try:
            response = self.session.request(
                method,
                self._url(path),
                headers=self.headers,
                json=payload,
                timeout=(10, 45),
            )
        except Exception as exc:
            raise StorageError("เชื่อมต่อ GitHub ไม่สำเร็จ กรุณาตรวจอินเทอร์เน็ต") from exc
        if response.status_code not in expected:
            message = ""
            try:
                message = str(response.json().get("message") or "")
            except (ValueError, AttributeError):
                pass
            safe_message = message[:180] or f"HTTP {response.status_code}"
            raise StorageError(f"GitHub ปฏิเสธคำขอ: {safe_message}")
        try:
            result = response.json()
        except ValueError as exc:
            raise StorageError("GitHub ส่งข้อมูลตอบกลับไม่ถูกต้อง") from exc
        if not isinstance(result, dict):
            raise StorageError("รูปแบบข้อมูลตอบกลับจาก GitHub ไม่ถูกต้อง")
        return result

    def validate_connection(self) -> dict[str, Any]:
        if self._validated_info is not None and time.monotonic() - self._validated_at < 300:
            return dict(self._validated_info)
        info = self._request("GET", "")
        private = bool(info.get("private"))
        self._repo_private = private
        if not private and not self.allow_public_storage:
            raise StorageConfigurationError(
                "Repository เก็บข้อมูลเป็น Public ระบบจึงหยุดเพื่อป้องกันรูปและข้อมูลรั่วไหล "
                "กรุณาใช้ Repository แบบ Private"
            )
        permissions = info.get("permissions") or {}
        if isinstance(permissions, dict) and not (permissions.get("push") or permissions.get("admin")):
            raise StorageConfigurationError("Token ไม่มีสิทธิ์ Contents: Read and write")
        validated = {
            "repository": self.repository,
            "branch": self.branch,
            "private": private,
            "default_branch": info.get("default_branch"),
        }
        self._validated_info = validated
        self._validated_at = time.monotonic()
        return dict(validated)

    def _create_blob(self, data: bytes) -> str:
        result = self._request(
            "POST",
            "/git/blobs",
            payload={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
            expected=(201,),
        )
        return str(result["sha"])

    def _read_json_path(self, path: str, ref: str) -> dict[str, Any] | None:
        safe_path = quote(str(path).lstrip("/"), safe="/")
        result = self._request(
            "GET",
            f"/contents/{safe_path}?ref={quote(ref, safe='')}",
            expected=(200, 404),
        )
        if "content" not in result:
            return None
        try:
            raw = base64.b64decode(str(result["content"]).replace("\n", ""))
            value = json.loads(raw.decode("utf-8"))
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _location_document(
        saved_metadata: dict[str, Any],
        image_path: str,
        metadata_path: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "barcode": str(saved_metadata.get("barcode") or ""),
            "product_name": str(saved_metadata.get("product_name") or ""),
            "location": normalize_location(saved_metadata.get("location")),
            "updated_at": str(saved_metadata.get("submitted_at") or ""),
            "submission_id": str(saved_metadata.get("submission_id") or ""),
            "employee_name": str(saved_metadata.get("employee_name") or ""),
            "image_path": image_path,
            "metadata_path": metadata_path,
            "location_changed": bool(saved_metadata.get("location_changed")),
            "previous_location": saved_metadata.get("previous_location"),
        }

    def save_submission(
        self,
        metadata: dict[str, Any],
        image_bytes: bytes,
    ) -> SubmissionResult:
        if self._repo_private is None:
            self.validate_connection()

        submission_id = _safe_segment(str(metadata.get("submission_id") or ""), "submission")
        image_path, metadata_path = submission_paths(
            self.data_root,
            str(metadata.get("barcode") or "barcode"),
            str(metadata.get("submitted_at") or "0000-00-00"),
            submission_id,
        )
        current_location = normalize_location(metadata.get("location"))
        has_location = is_complete_location(current_location)
        current_path = location_index_path(self.data_root, str(metadata.get("barcode") or "barcode"))
        image_blob = self._create_blob(image_bytes)
        ref_path = f"/git/ref/heads/{quote(self.branch, safe='')}"

        for attempt in range(4):
            ref = self._request("GET", ref_path)
            parent_sha = str(ref["object"]["sha"])
            parent_commit = self._request("GET", f"/git/commits/{parent_sha}")
            base_tree_sha = str(parent_commit["tree"]["sha"])
            previous_document = self._read_json_path(current_path, parent_sha) if has_location else None
            previous_location = normalize_location((previous_document or {}).get("location"))
            previous_complete = is_complete_location(previous_location)
            location_changed = locations_differ(previous_location, current_location)
            saved_metadata = dict(metadata)
            saved_metadata.update(
                {
                    "submission_id": submission_id,
                    "image_path": image_path,
                    "metadata_path": metadata_path,
                    "storage_repository": self.repository,
                    "storage_branch": self.branch,
                    "location": current_location if has_location else {},
                    "location_changed": location_changed,
                    "previous_location": previous_location if previous_complete else None,
                }
            )
            metadata_bytes = json.dumps(
                saved_metadata,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            metadata_blob = self._create_blob(metadata_bytes)
            tree_entries = [
                {"path": image_path, "mode": "100644", "type": "blob", "sha": image_blob},
                {"path": metadata_path, "mode": "100644", "type": "blob", "sha": metadata_blob},
            ]
            if has_location:
                location_bytes = json.dumps(
                    self._location_document(saved_metadata, image_path, metadata_path),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8")
                location_blob = self._create_blob(location_bytes)
                tree_entries.append(
                    {"path": current_path, "mode": "100644", "type": "blob", "sha": location_blob}
                )
            tree = self._request(
                "POST",
                "/git/trees",
                payload={
                    "base_tree": base_tree_sha,
                    "tree": tree_entries,
                },
                expected=(201,),
            )
            commit = self._request(
                "POST",
                "/git/commits",
                payload={
                    "message": f"product submission {metadata.get('barcode', '')} [{submission_id}]",
                    "tree": tree["sha"],
                    "parents": [parent_sha],
                },
                expected=(201,),
            )
            try:
                response = self.session.patch(
                    self._url(ref_path),
                    headers=self.headers,
                    json={"sha": commit["sha"], "force": False},
                    timeout=(10, 45),
                )
            except Exception as exc:
                raise StorageError("เชื่อมต่อ GitHub ไม่สำเร็จ กรุณาตรวจอินเทอร์เน็ต") from exc
            if response.status_code == 200:
                return SubmissionResult(
                    submission_id=submission_id,
                    image_path=image_path,
                    metadata_path=metadata_path,
                    repository=self.repository,
                    commit_sha=str(commit["sha"]),
                    location_changed=location_changed,
                    previous_location=previous_location if previous_complete else None,
                    current_location=current_location if has_location else None,
                )
            if response.status_code not in {409, 422}:
                raise StorageError(f"บันทึก GitHub ไม่สำเร็จ: HTTP {response.status_code}")
            time.sleep(0.35 * (attempt + 1))

        raise StorageError("มีพนักงานส่งข้อมูลพร้อมกันจำนวนมาก กรุณากดส่งอีกครั้ง")

    def list_submissions(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._repo_private is None:
            self.validate_connection()
        tree = self._request(
            "GET",
            f"/git/trees/{quote(self.branch, safe='')}?recursive=1",
        )
        if tree.get("truncated"):
            raise StorageError("รายการใน Repository มีจำนวนมากเกินขอบเขต GitHub Tree API")
        entries = tree.get("tree") or []
        metadata_entries = [
            item
            for item in entries
            if isinstance(item, dict)
            and item.get("type") == "blob"
            and str(item.get("path") or "").startswith(f"{self.data_root}/")
            and f"{self.data_root}/_locations/" not in str(item.get("path") or "")
            and str(item.get("path") or "").endswith(".json")
        ]
        metadata_entries.sort(key=lambda item: str(item.get("path") or ""), reverse=True)
        results: list[dict[str, Any]] = []
        for item in metadata_entries[: max(1, min(int(limit), 200))]:
            blob = self._request("GET", f"/git/blobs/{item['sha']}")
            try:
                raw = base64.b64decode(str(blob["content"]).replace("\n", ""))
                value = json.loads(raw.decode("utf-8"))
            except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                results.append(value)
        results.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
        return results

    def get_file_bytes(self, path: str) -> bytes:
        safe_path = quote(str(path).lstrip("/"), safe="/")
        result = self._request("GET", f"/contents/{safe_path}?ref={quote(self.branch, safe='')}")
        try:
            return base64.b64decode(str(result["content"]).replace("\n", ""))
        except (KeyError, ValueError) as exc:
            raise StorageError("อ่านไฟล์จาก GitHub ไม่สำเร็จ") from exc


class LocalSubmissionStore:
    """Local-only storage for Windows testing; not durable on Streamlit Cloud."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = "LOCAL"

    def validate_connection(self) -> dict[str, Any]:
        return {"repository": str(self.root), "branch": "local", "private": True}

    def save_submission(self, metadata: dict[str, Any], image_bytes: bytes) -> SubmissionResult:
        submission_id = _safe_segment(str(metadata.get("submission_id") or ""), "submission")
        image_path, metadata_path = submission_paths(
            "submissions",
            str(metadata.get("barcode") or "barcode"),
            str(metadata.get("submitted_at") or "0000-00-00"),
            submission_id,
        )
        image_target = self.root / image_path
        metadata_target = self.root / metadata_path
        image_target.parent.mkdir(parents=True, exist_ok=True)
        current_location = normalize_location(metadata.get("location"))
        has_location = is_complete_location(current_location)
        current_path = location_index_path("submissions", str(metadata.get("barcode") or "barcode"))
        current_target = self.root / current_path
        previous_document: dict[str, Any] | None = None
        if has_location and current_target.exists():
            try:
                loaded = json.loads(current_target.read_text(encoding="utf-8"))
                previous_document = loaded if isinstance(loaded, dict) else None
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                previous_document = None
        previous_location = normalize_location((previous_document or {}).get("location"))
        previous_complete = is_complete_location(previous_location)
        location_changed = locations_differ(previous_location, current_location)
        saved_metadata = dict(metadata)
        saved_metadata.update(
            {
                "submission_id": submission_id,
                "image_path": image_path,
                "metadata_path": metadata_path,
                "storage_repository": "LOCAL",
                "storage_branch": "local",
                "location": current_location if has_location else {},
                "location_changed": location_changed,
                "previous_location": previous_location if previous_complete else None,
            }
        )
        image_temp = image_target.with_suffix(".jpg.part")
        metadata_temp = metadata_target.with_suffix(".json.part")
        current_temp = current_target.with_suffix(".json.part")
        image_temp.write_bytes(image_bytes)
        metadata_temp.write_text(
            json.dumps(saved_metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if has_location:
            current_target.parent.mkdir(parents=True, exist_ok=True)
            current_temp.write_text(
                json.dumps(
                    GitHubSubmissionStore._location_document(saved_metadata, image_path, metadata_path),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        os.replace(image_temp, image_target)
        os.replace(metadata_temp, metadata_target)
        if has_location:
            os.replace(current_temp, current_target)
        return SubmissionResult(
            submission_id=submission_id,
            image_path=image_path,
            metadata_path=metadata_path,
            repository="LOCAL",
            location_changed=location_changed,
            previous_location=previous_location if previous_complete else None,
            current_location=current_location if has_location else None,
        )

    def list_submissions(self, limit: int = 100) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("submissions/**/*.json"), reverse=True):
            if "_locations" in path.parts:
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                results.append(value)
            if len(results) >= max(1, min(int(limit), 200)):
                break
        results.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
        return results

    def get_current_location(self, barcode: str) -> dict[str, Any] | None:
        target = self.root / location_index_path("submissions", barcode)
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def get_file_bytes(self, path: str) -> bytes:
        target = (self.root / str(path).lstrip("/"))
        resolved = target.resolve()
        if self.root not in resolved.parents:
            raise StorageError("ตำแหน่งไฟล์ไม่ปลอดภัย")
        try:
            return resolved.read_bytes()
        except OSError as exc:
            raise StorageError("อ่านไฟล์ในเครื่องไม่สำเร็จ") from exc
