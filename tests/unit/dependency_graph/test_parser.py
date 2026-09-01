"""Test suite for the dependency graph submodule."""

from pathlib import Path
from uuid import UUID

import pytest

from superset_io.dependency_graph.assets import Asset, AssetType
from superset_io.dependency_graph.parser import AssetsParser


class TestAssetsParser:
    """Tests for AssetsParser class."""

    @pytest.fixture
    def parser(self, tmp_path):
        """Create parser with temp folder."""
        return AssetsParser(tmp_path / "assets")

    @pytest.fixture
    def metadata_file(self, parser: AssetsParser) -> Path:
        """Helper to create metadata.yaml file."""
        metadata_file = parser.folder / "metadata.yaml"
        metadata_file.parent.mkdir(parents=True, exist_ok=True)
        return metadata_file

    def test_parser_init(self, tmp_path):
        """Parser should store folder path correctly."""
        parser = AssetsParser(tmp_path / "assets")
        assert parser.folder == (tmp_path / "assets").expanduser().resolve()

    def test_parse_missing_folder_raises(self, parser):
        """parse() should raise FileNotFoundError if folder doesn't exist."""
        with pytest.raises(FileNotFoundError, match="Assets folder does not exist"):
            parser.parse()

    def test_parse_missing_metadata_yaml_raises(self, parser):
        """parse() should raise FileNotFoundError if metadata.yaml is missing."""
        parser.folder.mkdir(parents=True, exist_ok=True)
        with pytest.raises(FileNotFoundError, match="Missing metadata.yaml"):
            parser.parse()

    def test_parse_invalid_metadata_type_raises(self, parser, metadata_file):
        """parse() should raise ValueError if metadata type is invalid."""
        metadata_file.write_text("type: invalid\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Invalid metadata.yaml"):
            parser.parse()

    def test_parse_empty_assets_folder(self, parser, metadata_file):
        """parse() should work with empty assets folder."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        parser.parse()

        graph = parser.graph
        assert graph.assets == set()

    def test_parse_database(self, parser, metadata_file):
        """parse() should correctly parse database files."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        databases_dir = parser.folder / "databases"
        databases_dir.mkdir()

        db_file = databases_dir / "db1.yaml"
        db_uuid = "a2dc77af-e654-49bb-b321-40f6b559a1ee"
        db_file.write_text(
            f"""
database_name: examples
uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        parser.parse()

        graph = parser.graph
        assert len(graph.assets) == 1
        assets = graph.assets_of_type(AssetType.DATABASE)
        assert len(assets) == 1

    def test_parse_dataset(self, parser, metadata_file):
        """parse() should correctly parse dataset files with database dependency."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        databases_dir = parser.folder / "databases"
        databases_dir.mkdir()

        db_file = databases_dir / "db1.yaml"
        db_uuid = "a2dc77af-e654-49bb-b321-40f6b559a1ee"
        db_file.write_text(
            f"""
database_name: examples
uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        datasets_dir = parser.folder / "datasets"
        datasets_dir.mkdir()

        ds_file = datasets_dir / "ds1.yaml"
        ds_uuid = "d95a2865-53ce-1f82-a53d-8e3c89331469"
        ds_file.write_text(
            f"""
table_name: test_table
uuid: {ds_uuid}
database_uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        parser.parse()

        graph = parser.graph
        assets = graph.assets
        assert len(assets) == 2

        db_asset = graph.get_asset(UUID(db_uuid))
        assert db_asset is not None

        ds_asset = graph.get_asset(UUID(ds_uuid))
        assert ds_asset is not None

        deps = graph.get_dependencies(ds_asset)
        assert db_asset in deps

    def test_parse_chart(self, parser, metadata_file):
        """parse() should correctly parse chart files with dataset dependency."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        databases_dir = parser.folder / "databases"
        databases_dir.mkdir()

        db_file = databases_dir / "db1.yaml"
        db_uuid = "a2dc77af-e654-49bb-b321-40f6b559a1ee"
        db_file.write_text(
            f"""
database_name: examples
uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        datasets_dir = parser.folder / "datasets"
        datasets_dir.mkdir()

        ds_file = datasets_dir / "ds1.yaml"
        ds_uuid = "d95a2865-53ce-1f82-a53d-8e3c89331469"
        ds_file.write_text(
            f"""
table_name: test_table
uuid: {ds_uuid}
database_uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        charts_dir = parser.folder / "charts"
        charts_dir.mkdir()

        chart_file = charts_dir / "chart1.yaml"
        chart_uuid = "11111111-2222-3333-4444-555555555555"
        chart_file.write_text(
            f"""
slice_name: test_chart
uuid: {chart_uuid}
dataset_uuid: {ds_uuid}
""",
            encoding="utf-8",
        )

        parser.parse()

        graph = parser.graph
        assets = graph.assets
        assert len(assets) == 3

        chart_asset = graph.get_asset(UUID(chart_uuid))
        assert chart_asset is not None

        ds_asset = graph.get_asset(UUID(ds_uuid))
        assert ds_asset is not None

        deps = graph.get_dependencies(chart_asset)
        assert ds_asset in deps

    def test_parse_dashboard(self, parser, metadata_file):
        """parse() should correctly parse dashboard files with chart dependencies."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        dashboards_dir = parser.folder / "dashboards"
        dashboards_dir.mkdir()

        dashboard_file = dashboards_dir / "dash1.yaml"
        dashboard_uuid = "22222222-3333-4444-5555-666666666666"
        chart_uuid = "11111111-2222-3333-4444-555555555555"
        theme_uuid = "33333333-4444-5555-6666-777777777777"
        dashboard_file.write_text(
            f"""
dashboard_title: test_dashboard
uuid: {dashboard_uuid}
theme_uuid: {theme_uuid}
position:
  CHART-1:
    type: CHART
    meta:
      chartId: 1
      sliceName: test_chart
      uuid: {chart_uuid}
""",
            encoding="utf-8",
        )

        parser.parse()

        graph = parser.graph
        assets = graph.assets
        assert len(assets) == 3

        dashboard_asset = graph.get_asset(UUID(dashboard_uuid))
        assert dashboard_asset is not None

        chart_asset = graph.get_asset(UUID(chart_uuid))
        assert chart_asset is not None

        theme_asset = graph.get_asset(UUID(theme_uuid))
        assert theme_asset is not None
        assert theme_asset.type == AssetType.THEME
        assert parser.asset_registry[theme_asset].name == "(theme not exported)"

        deps = graph.get_dependencies(dashboard_asset)
        assert chart_asset in deps
        assert theme_asset in deps

    def test_parse_charts_folder_with_multiple_files(self, parser, metadata_file):
        """parse() should correctly parse multiple chart files."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        charts_dir = parser.folder / "charts"
        charts_dir.mkdir()

        for i in range(3):
            chart_file = charts_dir / f"chart{i}.yaml"
            chart_uuid = f"00000000-0000-0000-0000-00000000000{i}"
            chart_file.write_text(
                f"""
slice_name: chart_{i}
uuid: {chart_uuid}
dataset_uuid: d95a2865-53ce-1f82-a53d-8e3c89331469
""",
                encoding="utf-8",
            )

        parser.parse()

        graph = parser.graph
        charts = graph.assets_of_type(AssetType.CHART)
        assert len(charts) == 3

    def test_parse_with_overwrite(self, parser: AssetsParser, metadata_file):
        """parse(overwrite=True) should re-parse even if already parsed."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        parser.parse()

        databases_dir = parser.folder / "databases"
        databases_dir.mkdir()

        db_file = databases_dir / "db1.yaml"
        db_uuid = "a2dc77af-e654-49bb-b321-40f6b559a1ee"
        db_file.write_text(
            f"""
database_name: examples
uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        parser.parse(overwrite=True)
        graph2 = parser.graph

        assert len(graph2.assets) == 1

    def test_parse_already_parsed_without_overwrite_raises(self, parser, metadata_file):
        """parse() should raise ValueError if already parsed without overwrite."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        parser.parse()

        with pytest.raises(ValueError, match="Already parsed"):
            parser.parse()

    def test_asset_registry(self, parser, metadata_file):
        """asset_registry should contain parsed asset metadata."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        databases_dir = parser.folder / "databases"
        databases_dir.mkdir()

        db_uuid = "a2dc77af-e654-49bb-b321-40f6b559a1ee"
        db_file = databases_dir / "db1.yaml"
        db_file.write_text(
            f"""
database_name: examples
uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        parser.parse()

        registry = parser.asset_registry
        assert len(registry) == 1

        db_asset = Asset(uuid=UUID(db_uuid), type=AssetType.DATABASE)
        assert db_asset in registry
        assert registry[db_asset].name == "examples"

    def test_asset_registry_multiple_types(self, parser, metadata_file):
        """asset_registry should contain metadata for all parsed asset types."""
        metadata_file.write_text("type: assets\n", encoding="utf-8")

        databases_dir = parser.folder / "databases"
        databases_dir.mkdir()

        db_uuid = "a2dc77af-e654-49bb-b321-40f6b559a1ee"
        db_file = databases_dir / "db1.yaml"
        db_file.write_text(
            f"""
database_name: examples
uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        datasets_dir = parser.folder / "datasets"
        datasets_dir.mkdir()

        ds_uuid = "d95a2865-53ce-1f82-a53d-8e3c89331469"
        ds_file = datasets_dir / "ds1.yaml"
        ds_file.write_text(
            f"""
table_name: test_table
uuid: {ds_uuid}
database_uuid: {db_uuid}
""",
            encoding="utf-8",
        )

        parser.parse()

        registry = parser.asset_registry
        assert len(registry) == 2
