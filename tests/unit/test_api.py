"""
Unit tests for the Superset API client.
"""

import io
import zipfile
from unittest.mock import Mock

import pytest
import requests

from superset_io.api import SupersetApiClient


def _resp(*, status_code=200, text="", json_data=None, raise_exc=None):
    r = Mock()
    r.status_code = status_code
    r.text = text
    r.json = Mock(return_value=json_data if json_data is not None else {})
    if raise_exc is None:
        r.raise_for_status = Mock()
    else:
        r.raise_for_status = Mock(side_effect=raise_exc)
    return r


def _http_error(msg="http error", *, response_text=""):
    err = requests.HTTPError(msg)
    if response_text:
        err.response = Mock()
        err.response.text = response_text
    return err


class TestAPITestConnection:
    """Lean unit tests for request-building logic in SupersetApiClient."""

    def test_connection_health_fails(self):
        session = Mock()
        session.base_url = "http://localhost:8088"
        session.headers = {"X-CSRFToken": "csrf"}  # shouldn't matter; should exit early

        session.get = Mock(
            side_effect=[
                _resp(raise_exc=_http_error("health down", response_text="nope"))
            ]
        )
        session.post = Mock()

        client = SupersetApiClient(session)
        assert client.test_connection() is False

        session.get.assert_called_once_with("/health")
        session.post.assert_not_called()

    def test_connection_log_fails(self):
        session = Mock()
        session.base_url = "http://localhost:8088"
        session.headers = {"X-CSRFToken": "csrf"}

        session.get = Mock(
            side_effect=[
                _resp(),  # /health ok
                _resp(
                    status_code=401, raise_exc=_http_error("unauthorized")
                ),  # /api/v1/log/ fails
            ]
        )
        session.post = Mock()

        client = SupersetApiClient(session)
        assert client.test_connection() is False

        assert session.get.call_args_list[0].args[0] == "/health"
        assert session.get.call_args_list[1].args[0] == "/api/v1/log/"
        session.post.assert_not_called()

    def test_connection_missing_csrf_header(self):
        session = Mock()
        session.base_url = "http://localhost:8088"
        session.headers = {}  # no X-CSRFToken

        session.get = Mock(side_effect=[_resp(), _resp()])
        session.post = Mock()

        client = SupersetApiClient(session)
        assert client.test_connection() is False

        session.post.assert_not_called()

    def test_connection_succeeds_with_invalid_payload(self):
        session = Mock()
        session.base_url = "http://localhost:8088/"
        session.headers = {"X-CSRFToken": "csrf"}

        session.get = Mock(side_effect=[_resp(), _resp()])

        post_res = _resp(
            status_code=422,
            text="unprocessable entity",
            json_data={"errors": [{"error_type": "INVALID_PAYLOAD_FORMAT_ERROR"}]},
            raise_exc=_http_error("422"),
        )
        session.post = Mock(return_value=post_res)

        client = SupersetApiClient(session)
        assert client.test_connection() is True

    def test_connection_error_type_not_expected(self):
        """
        This covers the 'POST fails and JSON doesn't match heuristic' => should return
        False.
        """
        session = Mock()
        session.base_url = "http://localhost:8088"
        session.headers = {"X-CSRFToken": "csrf"}

        session.get = Mock(side_effect=[_resp(), _resp()])

        post_res = _resp(
            status_code=403,
            text="forbidden",
            json_data={"errors": [{"error_type": "SOME_OTHER_ERROR"}]},
            raise_exc=_http_error("403"),
        )
        session.post = Mock(return_value=post_res)

        client = SupersetApiClient(session)

        # If your current code has the `e` NameError, this will raise.
        # Once code is fixed, it should simply be False.

        assert client.test_connection() is False

    def test_connection_post_succeeds(self):
        session = Mock()
        session.base_url = "http://localhost:8088"
        session.headers = {"X-CSRFToken": "csrf"}

        session.get = Mock(side_effect=[_resp(), _resp()])
        session.post = Mock(return_value=_resp(status_code=200))

        client = SupersetApiClient(session)
        assert client.test_connection() is True

        session.post.assert_called_once_with(
            "/api/v1/assets/import/",
            json={},
            headers={"Referer": "http://localhost:8088/"},
        )


class TestUploadAssets:
    @pytest.fixture
    def asset_folder(self, tmp_path):
        assets_root = tmp_path / "assets_folder"
        (assets_root / "dashboards").mkdir(parents=True)

        (assets_root / "metadata.yaml").write_text(
            "version: 1.0\ntype: assets", encoding="utf-8"
        )
        (assets_root / "dashboards" / "demo.yaml").write_text(
            "dashboard_title: Demo", encoding="utf-8"
        )
        yield assets_root

    @pytest.fixture
    def client(self):
        session = Mock()
        session.base_url = "http://localhost:8088"
        session.headers = {"X-CSRFToken": "csrf"}
        return SupersetApiClient(session)

    def test_upload_assets_zip(
        self, tmp_path, asset_folder, client: SupersetApiClient, monkeypatch
    ):
        # Create a real zip on disk from the fixture folder contents
        zip_path = tmp_path / "assets_bundle.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for p in asset_folder.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=p.relative_to(asset_folder.parent).as_posix())

        post_mock = Mock()
        monkeypatch.setattr(client.assets, "_import", post_mock)

        client.assets.upload(zip_path)

        post_mock.assert_called_once()
        buf = post_mock.call_args.kwargs["zipfile_buffer"]

        assert isinstance(buf, io.BytesIO)
        # ensure zipped structure contains expected files
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            assert "assets_folder/metadata.yaml" in names
            assert "assets_folder/dashboards/demo.yaml" in names

    def test_upload_assets_folder(
        self, asset_folder, client: SupersetApiClient, monkeypatch
    ):
        post_mock = Mock()
        monkeypatch.setattr(client.assets, "_import", post_mock)

        client.assets.upload(asset_folder)

        post_mock.assert_called_once()
        buf = post_mock.call_args.kwargs["zipfile_buffer"]

        assert isinstance(buf, io.BytesIO)
        # ensure zipped structure contains expected files
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            assert "assets_folder/metadata.yaml" in names
            assert "assets_folder/dashboards/demo.yaml" in names


class TestDownloadAssets:
    @pytest.fixture
    def client(self):
        session = Mock()
        session.base_url = "http://localhost:8088"
        session.headers = {"X-CSRFToken": "csrf"}
        return SupersetApiClient(session)

    def _zip_response_mock(self, files: dict[str, str]) -> requests.Response:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        zip_bytes = buf.getvalue()
        res = Mock()
        res.content = zip_bytes
        return res

    def test_download_assets_writes_zip_file(
        self, tmp_path, client: SupersetApiClient, monkeypatch
    ):
        metadata_content = "version: 1.0"
        dashboard_content = "dashboard_title: Demo"
        res_mock = self._zip_response_mock(
            {
                "assets_export_123/metadata.yaml": metadata_content,
                "assets_export_123/dashboards/demo.yaml": dashboard_content,
            }
        )
        monkeypatch.setattr(client.assets, "_export", Mock(return_value=res_mock))

        out_zip = tmp_path / "out.zip"
        client.assets.download(out_zip)

        assert out_zip.exists()

        # we sanitize the zip, so we cannot compare byte by byte. check content instead.
        with zipfile.ZipFile(out_zip, "r") as zf:
            assert "assets_export_123/metadata.yaml" in zf.namelist()
            assert "assets_export_123/dashboards/demo.yaml" in zf.namelist()
            assert (
                zf.read("assets_export_123/metadata.yaml").decode() == metadata_content
            )
            assert (
                zf.read("assets_export_123/dashboards/demo.yaml").decode()
                == dashboard_content
            )

    def test_download_assets_extracts_to_folder_and_moves_children(
        self, tmp_path, client: SupersetApiClient, monkeypatch
    ):
        res_mock = self._zip_response_mock(
            {
                "assets_export_123/metadata.yaml": "version: 1.0",
                "assets_export_123/dashboards/demo.yaml": "dashboard_title: Demo",
            }
        )
        monkeypatch.setattr(client.assets, "_export", Mock(return_value=res_mock))

        out_dir = tmp_path / "out_folder"  # no .zip suffix => folder mode
        client.assets.download(out_dir)

        assert out_dir.is_dir()
        # children moved out of assets_export_123/ into out_dir
        assert (out_dir / "metadata.yaml").exists()
        assert (out_dir / "dashboards" / "demo.yaml").exists()

    def test_download_assets_folder_destination_not_empty_raises(
        self, tmp_path, client: SupersetApiClient
    ):
        out_dir = tmp_path / "out_folder"
        out_dir.mkdir(parents=True)
        (out_dir / "already_there.txt").write_text("x", encoding="utf-8")

        with pytest.raises(ValueError, match="is not empty"):
            client.assets.download(out_dir)

    def test_download_assets_raises_if_no_assets_export_folder_in_zip(
        self, tmp_path, client: SupersetApiClient, monkeypatch
    ):
        res_mock = self._zip_response_mock(
            {
                "not_assets_export/metadata.yaml": "version: 1.0",
            }
        )
        monkeypatch.setattr(client.assets, "_export", Mock(return_value=res_mock))

        out_dir = tmp_path / "out_folder"
        with pytest.raises(
            ValueError, match="Did not find a unique `assets_export` folder"
        ):
            client.assets.download(out_dir)

    def test_download_assets_raises_if_multiple_assets_export_folders_in_zip(
        self, tmp_path, client: SupersetApiClient, monkeypatch
    ):
        res_mock = self._zip_response_mock(
            {
                "assets_export_111/metadata.yaml": "version: 1.0",
                "assets_export_222/metadata.yaml": "version: 1.0",
            }
        )
        monkeypatch.setattr(client.assets, "_export", Mock(return_value=res_mock))

        out_dir = tmp_path / "out_folder"
        with pytest.raises(
            ValueError, match="Did not find a unique `assets_export` folder"
        ):
            client.assets.download(out_dir)
