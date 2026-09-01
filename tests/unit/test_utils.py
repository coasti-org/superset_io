"""
Unit tests for utility functions.
"""

import io
import zipfile
from importlib import metadata
from pathlib import Path

import pytest

from superset_io.utils import (
    get_version,
    sanitize_assets_bundle,
    validate_assets_bundle_structure,
    zipfile_buffer_from_folder,
    zipfile_buffer_from_zipfile,
)


class TestZipfileBufferFromFolder:
    """Tests for zipfile_buffer_from_folder function."""

    def test_create_zip_from_folder(self, tmp_path):
        """Test creating a ZIP from a folder structure."""
        # Create a test folder structure
        test_dir = tmp_path / "test_folder"
        test_dir.mkdir()

        # Create some files
        (test_dir / "file1.txt").write_text("Content 1")
        (test_dir / "file2.txt").write_text("Content 2")
        subdir = test_dir / "subdir"
        subdir.mkdir()
        (subdir / "file3.txt").write_text("Content 3")

        # Create ZIP
        zip_buffer = zipfile_buffer_from_folder(test_dir)

        # Verify the ZIP
        assert isinstance(zip_buffer, io.BytesIO)

        with zipfile.ZipFile(zip_buffer, "r") as zf:
            assert "test_folder/file1.txt" in zf.namelist()
            assert "test_folder/file2.txt" in zf.namelist()
            assert "test_folder/subdir/file3.txt" in zf.namelist()

            # Verify file contents
            assert zf.read("test_folder/file1.txt").decode() == "Content 1"
            assert zf.read("test_folder/file2.txt").decode() == "Content 2"
            assert zf.read("test_folder/subdir/file3.txt").decode() == "Content 3"

    def test_nonexistent_folder(self):
        """Test that non-existent folder raises ValueError."""
        with pytest.raises(ValueError, match="Not a folder"):
            zipfile_buffer_from_folder("/nonexistent/path")

    def test_file_instead_of_folder(self, tmp_path):
        """Test that passing a file instead of folder raises ValueError."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")

        with pytest.raises(ValueError, match="Not a folder"):
            zipfile_buffer_from_folder(test_file)


class TestZipfileBufferFromZipfile:
    """Tests for zipfile_buffer_from_zipfile function."""

    def test_copy_existing_zip(self, tmp_path):
        """Test copying an existing ZIP file to a buffer."""
        # Create a test ZIP file
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("file1.txt", "Content 1")
            zf.writestr("subdir/file2.txt", "Content 2")

        # Copy to buffer
        zip_buffer = zipfile_buffer_from_zipfile(zip_path)

        # Verify the buffer
        assert isinstance(zip_buffer, io.BytesIO)

        with zipfile.ZipFile(zip_buffer, "r") as zf:
            assert "file1.txt" in zf.namelist()
            assert "subdir/file2.txt" in zf.namelist()
            assert zf.read("file1.txt").decode() == "Content 1"

    def test_nonexistent_zip(self):
        """Test that non-existent ZIP file raises error on read."""
        # The function will try to open the file, which will raise FileNotFoundError
        # when the file is read. The function doesn't check existence beforehand.
        non_existent = Path("/nonexistent/path.zip")

        # This will raise FileNotFoundError when trying to open the file
        with pytest.raises(FileNotFoundError):
            zipfile_buffer_from_zipfile(non_existent)


class TestValidateAssetsBundleStructure:
    """Tests for validate_assets_bundle_structure function."""

    def test_valid_structure(self):
        """Test validation of a valid Superset assets bundle."""
        # Create a valid ZIP structure
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("assets_export_20240101/metadata.yaml", "version: 1.0")
            zf.writestr("assets_export_20240101/dashboards/test.yaml", "title: Test")
            zf.writestr("assets_export_20240101/charts/", "")  # Empty directory
            zf.writestr("assets_export_20240101/datasets/", "")

        zip_buffer.seek(0)

        # Should not raise any exception
        validate_assets_bundle_structure(zip_buffer)

    def test_valid_structure_with_bytes(self):
        """Test validation with bytes input."""
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("assets_export_20240101/metadata.yaml", "version: 1.0")
            zf.writestr("assets_export_20240101/dashboards/test.yaml", "title: Test")

        zip_bytes = zip_buffer.getvalue()

        # Should not raise any exception
        validate_assets_bundle_structure(zip_bytes)

    def test_valid_structure_with_path(self, tmp_path):
        """Test validation with Path input."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("assets_export_20240101/metadata.yaml", "version: 1.0")
            zf.writestr("assets_export_20240101/dashboards/test.yaml", "title: Test")

        # Should not raise any exception
        validate_assets_bundle_structure(zip_path)

    def test_missing_metadata(self):
        """Test validation fails when metadata.yaml is missing."""
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("assets_export_20240101/dashboards/test.yaml", "title: Test")

        zip_buffer.seek(0)

        with pytest.raises(ValueError, match="Missing metadata.yaml"):
            validate_assets_bundle_structure(zip_buffer)

    def test_metadata_not_in_root_folder(self):
        """Test validation fails when metadata.yaml is not in root folder."""
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata.yaml", "version: 1.0")  # Not in root folder
            zf.writestr("dashboards/test.yaml", "title: Test")
            zf.writestr("charts/test.yaml", "title: Test")

        zip_buffer.seek(0)

        with pytest.raises(ValueError, match="Expected exactly one top-level folder"):
            validate_assets_bundle_structure(zip_buffer)

    def test_multiple_root_folders(self):
        """Test validation fails with multiple top-level folders."""
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("folder1/metadata.yaml", "version: 1.0")
            zf.writestr("folder2/dashboards/test.yaml", "title: Test")

        zip_buffer.seek(0)

        with pytest.raises(ValueError, match="Expected exactly one top-level folder"):
            validate_assets_bundle_structure(zip_buffer)

    def test_metadata_in_wrong_location(self):
        """Test validation fails when metadata.yaml is not at expected path."""
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # Also add another metadata in wrong place (should still pass)
            zf.writestr("assets_export_20240101/another/metadata.yaml", "version: 1.0")

        zip_buffer.seek(0)

        # This should still pass because we have metadata at the expected path
        with pytest.raises(
            ValueError, match="metadata.yaml not found at expected path"
        ):
            validate_assets_bundle_structure(zip_buffer)

    def test_empty_zip(self):
        """Test validation fails with empty ZIP."""
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED):
            pass  # Empty zip

        zip_buffer.seek(0)

        with pytest.raises(ValueError, match="Missing metadata.yaml"):
            validate_assets_bundle_structure(zip_buffer)


class TestGetVersion:
    def test_get_version_returns_version_string(self, monkeypatch):
        monkeypatch.setattr(metadata, "version", lambda name: "1.2.3")

        assert get_version() == "1.2.3"

    def test_get_version_returns_fallback_when_package_not_found(self, monkeypatch):
        def _raise(_name):
            raise metadata.PackageNotFoundError

        monkeypatch.setattr(metadata, "version", _raise)

        assert get_version() == "[not found] Use `uv sync` when developing!"


class TestSanitizeAssetsBundle:
    """Tests for sanitize_assets_bundle and _sanitize_asset_file."""

    def _make_folder(self, tmp_path, files: dict[str, str]):
        """Helper to create a folder structure from a dict of path->yaml_content."""
        for rel_path, content in files.items():
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return tmp_path

    def test_renames_file_by_uuid(self, tmp_path):
        """Asset file with uuid should be renamed to <uuid>.yaml and lower cased."""
        folder = self._make_folder(
            tmp_path,
            {
                "dashboards/demo.yaml": (
                    "dashboard_title: Demo\n"
                    "uuid: A1B2C3D4-5678-4ABC-DEF0-123456789ABC\n"
                ),
            },
        )

        sanitize_assets_bundle(folder)

        old = folder / "dashboards" / "demo.yaml"
        new = folder / "dashboards" / "a1b2c3d4-5678-4abc-def0-123456789abc.yaml"
        assert not old.exists()
        assert new.exists()

    def test_handles_mixed_asset_types(self, tmp_path):
        """Multiple asset subfolders are all processed."""
        folder = self._make_folder(
            tmp_path,
            {
                "dashboards/demo.yaml": (
                    "dashboard_title: Demo\n"
                    "uuid: 11111111-aaaa-4bbb-cccc-dddddddddddd\n"
                ),
                "charts/area.yaml": (
                    "slice_name: Area\nuuid: 22222222-bbbb-4ccc-dddd-eeeeeeeeeeee\n"
                ),
                "databases/sqlite.yaml": (
                    "database_name: SQLite\n"
                    "uuid: 33333333-cccc-4ddd-eeee-ffffffffffff\n"
                ),
            },
        )

        sanitize_assets_bundle(folder)

        assert not (folder / "dashboards" / "demo.yaml").exists()
        assert not (folder / "charts" / "area.yaml").exists()
        assert not (folder / "databases" / "sqlite.yaml").exists()
        assert (
            folder / "dashboards" / "11111111-aaaa-4bbb-cccc-dddddddddddd.yaml"
        ).exists()
        assert (
            folder / "charts" / "22222222-bbbb-4ccc-dddd-eeeeeeeeeeee.yaml"
        ).exists()
        assert (
            folder / "databases" / "33333333-cccc-4ddd-eeee-ffffffffffff.yaml"
        ).exists()

    def test_charts_remove_query_context(self, tmp_path):
        """Chart assets should drop query_context during sanitization."""
        folder = self._make_folder(
            tmp_path,
            {
                "charts/area.yaml": (
                    "slice_name: Area\n"
                    "uuid: 44444444-aaaa-4bbb-cccc-111111111111\n"
                    'query_context: \'{"foo": "bar"}\'\n'
                ),
            },
        )

        sanitize_assets_bundle(folder)

        chart_file = folder / "charts" / "44444444-aaaa-4bbb-cccc-111111111111.yaml"
        assert chart_file.exists()
        chart_content = chart_file.read_text(encoding="utf-8")
        assert "query_context:" not in chart_content
        assert "slice_name: Area" in chart_content
        assert "uuid: 44444444-aaaa-4bbb-cccc-111111111111" in chart_content

    def test_leaves_non_asset_subfolders_untouched(self, tmp_path):
        """Files outside charts/dashboards/datasets/databases are skipped."""
        folder = self._make_folder(
            tmp_path,
            {
                "metadata.yaml": "version: 1.0\ntype: assets\n",
                "README.txt": "some notes",
            },
        )

        sanitize_assets_bundle(folder)

        assert (folder / "metadata.yaml").exists()
        assert (folder / "README.txt").exists()

    def test_skips_files_without_uuid(self, tmp_path):
        """Files without uuid field should be left untouched (not renamed)."""
        folder = self._make_folder(
            tmp_path,
            {
                "dashboards/no_uuid.yaml": "dashboard_title: No UUID\n",
            },
        )

        sanitize_assets_bundle(folder)

        # File stays in place because it has no uuid
        assert (folder / "dashboards" / "no_uuid.yaml").exists()

    def test_skips_metadata_yaml(self, tmp_path):
        """metadata.yaml is always skipped regardless of content."""
        metadata_content = "version: 1.0\ntype: assets\n"
        dashboard_content = (
            "dashboard_title: Demo\nuuid: deadbeef-1234-4abc-def0-123456789abc\n"
        )
        folder = self._make_folder(
            tmp_path,
            {
                "metadata.yaml": metadata_content,
                "dashboards/demo.yaml": dashboard_content,
            },
        )

        sanitize_assets_bundle(folder)

        renamed_dashboard = (
            folder / "dashboards" / "deadbeef-1234-4abc-def0-123456789abc.yaml"
        )

        assert (folder / "metadata.yaml").exists()
        assert (folder / "metadata.yaml").read_text(
            encoding="utf-8"
        ) == metadata_content
        assert not (folder / "dashboards" / "demo.yaml").exists()
        assert renamed_dashboard.exists()
        assert renamed_dashboard.read_text(encoding="utf-8") == dashboard_content

    def test_handles_collision_by_unlinking_existing(self, tmp_path):
        """If a file with the target UUID name already exists, it gets replaced."""
        # Pre-create a file with the uuid-based name
        (tmp_path / "dashboards").mkdir(parents=True)
        existing = tmp_path / "dashboards" / "deadbeef-1234-4abc-def0-123456789abc.yaml"
        existing.write_text(
            "existing: true\nuuid: deadbeef-1234-4abc-def0-123456789abc\n",
            encoding="utf-8",
        )

        # Also create the source file that would rename to the same UUID
        dup_source = tmp_path / "dashboards" / "old_name.yaml"
        dup_source.write_text(
            "dashboard_title: Dup\nuuid: deadbeef-1234-4abc-def0-123456789abc\n",
            encoding="utf-8",
        )

        sanitize_assets_bundle(tmp_path)

        # Source should be gone, target should exist
        assert not dup_source.exists()
        assert existing.exists()

    def test_nonexistent_folder_raises(self):
        """sanitize_assets_bundle raises ValueError for non-existent folder."""
        with pytest.raises(ValueError, match="Not a folder"):
            sanitize_assets_bundle(Path("/nonexistent/path"))
