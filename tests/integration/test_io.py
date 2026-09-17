"""
Integration tests for dashboard export and import functionality.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from superset_io.api import SupersetApiClient
from superset_io.cli import app
from superset_io.dependency_graph import AssetsParser

from .superset_instance import SupersetInstance


def _upload_with_cli(
    superset_instance: SupersetInstance,
    source_path: Path,
    *options: str,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "--base-url",
            superset_instance.url,
            "--username",
            superset_instance.user,
            "--password",
            superset_instance.password,
            "upload",
            str(source_path),
            "--yes",
            *options,
        ],
    )
    assert result.exit_code == 0, result.stdout


@pytest.mark.integration
class TestApiClient:
    """Integration tests for dashboard export and import."""

    @pytest.fixture(autouse=True)
    def seed_db(self, superset_client: SupersetApiClient):
        """We start with an empty database, so we need to seed it with some assets."""
        charts = superset_client.charts.get_all()
        for chart in charts["result"]:
            superset_client.charts.remove(chart["id"])

        dashboards = superset_client.dashboards.get_all()
        for dashboard in dashboards["result"]:
            superset_client.dashboards.remove(dashboard["id"])

        datasets = superset_client.datasets.get_all()
        for dataset in datasets["result"]:
            superset_client.datasets.remove(dataset["id"])

        databases = superset_client.databases.get_all()
        for database in databases["result"]:
            superset_client.databases.remove(database["id"])

    def test_io_roundtrip(self, tmp_path, superset_client: SupersetApiClient):
        """Test upload of valid assets."""

        # Upload folder
        superset_client.assets.upload(
            Path(__file__).parent.parent / "assets" / "sample_assets"
        )

        # All assets should now be available
        dashboards = superset_client.dashboards.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], dashboards["result"])
        assert "00000000-0000-0000-0000-da54b0aad000" in uuids

        # Download folder
        superset_client.assets.download(tmp_path)

        # The uploaded dashboard should be included in the downloaded
        # assets
        assert (tmp_path / "dashboards" / "Test_Dash_1.yaml").exists()

        # Assert same content
        assets_dl = AssetsParser(tmp_path)
        assets_original = AssetsParser(
            Path(__file__).parent.parent / "assets" / "sample_assets"
        )
        assets_dl.parse()
        assets_original.parse()

        assert assets_dl.graph == assets_original.graph

    def test_upload_select(self, superset_client: SupersetApiClient, superset_instance):
        """Test upload of valid assets."""

        # Upload folder with select
        _upload_with_cli(
            superset_instance,
            Path(__file__).parent.parent / "assets" / "sample_assets",
            "--select",
            "00000000-0000-0000-0000-da54b0aad000",
        )

        # Should include selected dashboard and its dependencies
        # (charts -> dataset -> database)
        charts = superset_client.charts.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], charts["result"])
        assert "c1a87000-0000-0000-0000-000000000000" in uuids

        dashboards = superset_client.dashboards.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], dashboards["result"])
        assert "00000000-0000-0000-0000-da54b0aad000" in uuids

        datasets = superset_client.datasets.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], datasets["result"])
        assert "00000000-da7a-5e70-0000-000000000000" in uuids

        databases = superset_client.databases.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], databases["result"])
        assert "00000000-da7a-ba5e-0000-000000000000" in uuids

    def test_upload_select_no_dependencies(
        self, superset_client: SupersetApiClient, superset_instance
    ):
        """Test upload of valid assets."""

        # Upload folder with select but no dependencies
        _upload_with_cli(
            superset_instance,
            Path(__file__).parent.parent / "assets" / "sample_assets",
            "--select",
            "00000000-0000-0000-0000-da54b0aad000",
            "--no-include-dependencies",
        )

        # Should include selected dashboard but not its dependencies
        charts = superset_client.charts.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], charts["result"])
        assert "c1a87000-0000-0000-0000-000000000000" not in uuids

        dashboards = superset_client.dashboards.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], dashboards["result"])
        assert "00000000-0000-0000-0000-da54b0aad000" in uuids

        datasets = superset_client.datasets.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], datasets["result"])
        assert "00000000-da7a-5e70-0000-000000000000" not in uuids

        databases = superset_client.databases.get_all()
        uuids: map[str] = map(lambda x: x["uuid"], databases["result"])
        assert "00000000-da7a-ba5e-0000-000000000000" not in uuids

    @pytest.mark.parametrize(
        "test_case",
        [
            {
                # Skip with no selection given. skip chart to avoid upload errors
                "selected": None,
                "skip": ["c1a87000-0000-0000-0000-000000000000"],
                "expected_include": [
                    "00000000-0000-0000-0000-da54b0aad000",
                    "00000000-da7a-5e70-0000-000000000000",
                    "00000000-da7a-ba5e-0000-000000000000",
                ],
                "expected_exclude": [
                    "c1a87000-0000-0000-0000-000000000000",
                ],
            },
            {
                # Skip an asset that was selected
                "selected": [
                    "00000000-0000-0000-0000-da54b0aad000",
                    "c1a87000-0000-0000-0000-000000000000",
                ],
                "skip": ["c1a87000-0000-0000-0000-000000000000"],
                "expected_include": ["00000000-0000-0000-0000-da54b0aad000"],
                "expected_exclude": ["c1a87000-0000-0000-0000-000000000000"],
            },
            {
                # Skip a dependency
                "selected": ["c1a87000-0000-0000-0000-000000000000"],
                "skip": [
                    "00000000-0000-0000-0000-da54b0aad000",
                ],
                "expected_include": [
                    "c1a87000-0000-0000-0000-000000000000",
                    "00000000-da7a-5e70-0000-000000000000",
                    "00000000-da7a-ba5e-0000-000000000000",
                ],
                "expected_exclude": [
                    "00000000-0000-0000-0000-da54b0aad000",
                ],
            },
        ],
    )
    def test_upload_skip(
        self, superset_client: SupersetApiClient, superset_instance, test_case
    ):
        """Test upload with skip parameter."""
        # Upload with specified selection and skip
        options = [
            option
            for asset_uuid in test_case["selected"] or []
            for option in ("--select", asset_uuid)
        ]
        options.extend(
            option
            for asset_uuid in test_case["skip"]
            for option in ("--skip", asset_uuid)
        )
        _upload_with_cli(
            superset_instance,
            Path(__file__).parent.parent / "assets" / "sample_assets",
            *options,
        )

        # Get uuids for all asset types
        uuids = []
        for asset_type in ["dashboards", "charts", "datasets", "databases"]:
            api_method = getattr(superset_client, asset_type)
            assets = api_method.get_all()
            uuids.extend([x["uuid"] for x in assets["result"]])

        for uuid in test_case["expected_include"]:
            assert uuid in uuids

        for uuid in test_case["expected_exclude"]:
            assert uuid not in uuids


@pytest.mark.integration
class TestDashboardEndpoints:
    @pytest.fixture(autouse=True)
    def seed_db(self, superset_client: SupersetApiClient):
        """Seed the database with a sample dashboard before each test."""
        # Upload the sample dashboard
        superset_client.assets.upload(
            Path(__file__).parent.parent / "assets" / "sample_assets"
        )

    @pytest.fixture
    def dashboard_uuid(self) -> str:
        """Return the UUID of the sample dashboard."""
        return "00000000-0000-0000-0000-da54b0aad000"

    @pytest.fixture
    def chart_uuid(self) -> str:
        """Return the UUID of the sample chart."""
        return "c1a87000-0000-0000-0000-000000000000"

    def test_get(
        self,
        superset_client: SupersetApiClient,
        dashboard_uuid: str,
    ) -> None:
        """Test that we can retrieve the sample dashboard."""
        dashboard = superset_client.dashboards.get(dashboard_uuid)
        assert dashboard is not None
        assert dashboard["result"]["uuid"] == dashboard_uuid

    def test_remove(
        self, superset_client: SupersetApiClient, dashboard_uuid: str, chart_uuid: str
    ) -> None:
        """Test that we can delete the sample dashboard."""
        superset_client.dashboards.remove(dashboard_uuid)

        # Attempting to retrieve the deleted dashboard should now fail
        with pytest.raises(Exception):
            superset_client.dashboards.get(dashboard_uuid)

        # This does not delete the underlying assets!
        charts = superset_client.charts.get(chart_uuid)
        assert charts is not None

    def test_get_all(self, superset_client: SupersetApiClient) -> None:
        """Test that we can retrieve the list of charts."""
        charts = superset_client.dashboards.get_all()
        assert isinstance(charts, dict)
        # The response should have a 'result' key with chart list
        assert "result" in charts
        assert isinstance(charts["result"], list)


@pytest.mark.integration
class TestChartsEndpoints:
    @pytest.fixture(autouse=True)
    def seed_db(self, superset_client: SupersetApiClient):
        """Seed the database with a sample dashboard before each test."""
        # Upload the sample dashboard
        superset_client.assets.upload(
            Path(__file__).parent.parent / "assets" / "sample_assets"
        )

    @pytest.fixture
    def chart_uuid(self) -> str:
        """Return the UUID of the sample chart."""
        return "c1a87000-0000-0000-0000-000000000000"

    def test_get(
        self,
        superset_client: SupersetApiClient,
        chart_uuid: str,
    ) -> None:
        """Test that we can retrieve the sample chart."""
        chart = superset_client.charts.get(chart_uuid)
        assert chart is not None
        assert chart["result"]["uuid"] == chart_uuid

    def test_remove(self, superset_client: SupersetApiClient, chart_uuid: str) -> None:
        """Test that we can delete the sample dashboard."""
        superset_client.charts.remove(chart_uuid)

        # Attempting to retrieve the deleted dashboard should now fail
        with pytest.raises(Exception):
            superset_client.charts.get(chart_uuid)

    def test_get_all(self, superset_client: SupersetApiClient) -> None:
        """Test that we can retrieve the list of charts."""
        charts = superset_client.charts.get_all()
        assert isinstance(charts, dict)
        # The response should have a 'result' key with chart list
        assert "result" in charts
        assert isinstance(charts["result"], list)


@pytest.mark.integration
class TestDatasetEndpoints:
    @pytest.fixture(autouse=True)
    def seed_db(self, superset_client: SupersetApiClient):
        """Seed the database with a sample dashboard before each test."""
        # Upload the sample dashboard
        superset_client.assets.upload(
            Path(__file__).parent.parent / "assets" / "sample_assets"
        )

    @pytest.fixture
    def dataset_uuid(self) -> str:
        """Return the UUID of the sample dataset."""
        return "00000000-da7a-5e70-0000-000000000000"

    def test_get(
        self,
        superset_client: SupersetApiClient,
        dataset_uuid: str,
    ) -> None:
        """Test that we can retrieve the sample dataset."""

        # Get by uuid
        dataset = superset_client.datasets.get(dataset_uuid)
        assert dataset is not None
        assert dataset["uuid"] == dataset_uuid

        # Get same again by id
        dataset_by_id = superset_client.datasets.get(dataset["id"])
        assert dataset_by_id is not None
        assert dataset_by_id["id"] == dataset["id"]

    def test_get_all(self, superset_client: SupersetApiClient) -> None:
        """Test that we can retrieve the list of datasets."""
        datasets = superset_client.datasets.get_all()
        assert isinstance(datasets, dict)
        # The response should have a 'result' key with dataset list
        assert "result" in datasets
        assert isinstance(datasets["result"], list)

    def test_remove(
        self, superset_client: SupersetApiClient, dataset_uuid: str
    ) -> None:
        """Test that we can delete the sample dataset."""
        superset_client.datasets.remove(dataset_uuid)

        # Attempting to retrieve the deleted dataset should now fail
        with pytest.raises(Exception):
            superset_client.datasets.get(dataset_uuid)


@pytest.mark.integration
class TestDatabaseEndpoints:
    @pytest.fixture(autouse=True)
    def seed_db(self, superset_client: SupersetApiClient):
        """Seed the database with a sample dashboard before each test."""
        # Upload the sample dashboard
        superset_client.assets.upload(
            Path(__file__).parent.parent / "assets" / "sample_assets"
        )

    @pytest.fixture
    def database_uuid(self) -> str:
        """Return the UUID of the sample database."""
        return "00000000-da7a-ba5e-0000-000000000000"

    def test_get(
        self,
        superset_client: SupersetApiClient,
        database_uuid: str,
    ) -> None:
        """Test that we can retrieve the sample database."""
        database = superset_client.databases.get(database_uuid)
        assert database is not None
        assert database["uuid"] == database_uuid

        # Get same again by id
        database_by_id = superset_client.databases.get(database["id"])
        assert database_by_id is not None
        assert database_by_id["id"] == database["id"]

    def test_get_all(self, superset_client: SupersetApiClient) -> None:
        """Test that we can retrieve the list of databases."""
        databases = superset_client.databases.get_all()
        assert isinstance(databases, dict)
        # The response should have a 'result' key with database list
        assert "result" in databases
        assert isinstance(databases["result"], list)

    def test_remove(
        self, superset_client: SupersetApiClient, database_uuid: str
    ) -> None:
        """Test that we can delete the sample database."""

        # A bit inconsistent but this only works if a no datasets are attached
        with pytest.raises(Exception, match="UNPROCESSABLE ENTITY for url"):
            superset_client.databases.remove(database_uuid)

        # Remove the attached dataset first
        dataset = superset_client.datasets.get_all()
        for ds in dataset["result"]:
            if ds["database"]["uuid"] == database_uuid:
                superset_client.datasets.remove(ds["uuid"])

        # Now we can remove the database
        superset_client.databases.remove(database_uuid)

        # Attempting to retrieve the deleted database should now fail
        with pytest.raises(Exception):
            superset_client.databases.get(database_uuid)
