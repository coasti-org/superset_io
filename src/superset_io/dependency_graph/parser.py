import logging
import os
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

from ruamel.yaml import YAML, YAMLError

from .assets import Asset, AssetData, AssetType
from .graph import DependencyGraph

log = logging.getLogger("superset_io")


class ParserError(Exception):
    def __init__(
        self, message: str, path: Path | None = None, *, cause: Exception | None = None
    ) -> None:
        self.message = message
        self.path = path
        self.cause = cause
        parts = [message]
        if path is not None:
            parts.append(f"path={path}")
        super().__init__(" | ".join(parts))


class _AssetChunk(NamedTuple):
    """Parser metadata for an asset, used in the parsing process."""

    asset: Asset
    dependencies: set[Asset]
    dependents: set[Asset]
    registry: dict[Asset, AssetData]


class AssetsParser:
    """Parses an Superset Assets folder into a graph
    or dependencies and a metadata lookup.
    """

    executor: type[ProcessPoolExecutor | ThreadPoolExecutor] | None
    folder: Path

    def __init__(self, folder: Path) -> None:
        self.executor = ProcessPoolExecutor
        self.folder = folder.expanduser().resolve()

    _graph: DependencyGraph | None = None
    # The parsed graph (once parsed)

    _asset_registry: dict[Asset, AssetData] | None = None
    # Metadata of each asset (once parsed)

    def parse(self, overwrite: bool = False):
        """Parse an assets folder into one dependency graphs."""

        if (
            self._graph is not None and self._asset_registry is not None
        ) and not overwrite:
            raise ValueError("Already parsed. Set overwrite=True to re-parse.")

        if not self.folder.exists():
            raise FileNotFoundError(f"Assets folder does not exist: {self.folder}")
        if not self.folder.is_dir():
            raise NotADirectoryError(f"Assets folder is not a directory: {self.folder}")

        # Check has metadata.yaml and type=assets
        metadata_file = self.folder / "metadata.yaml"
        if not metadata_file.exists():
            raise FileNotFoundError(
                f"Missing metadata.yaml in assets folder: {metadata_file}"
            )

        # Validate metadata.yaml
        metadata = self.__load_yaml(metadata_file)
        if metadata.get("type") != "assets":
            raise ValueError(
                f"Invalid metadata.yaml (expected type: assets): {metadata_file}"
            )

        # Parse
        self._graph, self._asset_registry = self._parse(self.folder)

    @property
    def graph(self) -> DependencyGraph:
        if self._graph is None:
            raise ValueError("Not parsed yet. Call parse() first.")
        return self._graph

    @property
    def asset_registry(self) -> dict[Asset, AssetData]:
        if self._asset_registry is None:
            raise ValueError("Not parsed yet. Call parse() first.")
        return self._asset_registry

    @staticmethod
    def __load_yaml(file: Path) -> dict[str, Any]:
        try:
            with file.open(encoding="utf-8") as f:
                data = YAML(typ="safe").load(f)
        except YAMLError as e:
            raise ParserError("Invalid YAML", file, cause=e) from e

        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ParserError("YAML root must be a mapping/object", file)
        return data

    @staticmethod
    def __parse_uuid(data: Any) -> UUID | None:
        if isinstance(data, str):
            return UUID(data)
        return None

    def _parse(self, folder: Path) -> tuple[DependencyGraph, dict[Asset, AssetData]]:
        if not self.executor:
            return self._parse_no_thread(folder)

        mapping = {
            "charts/**/*.yaml": self._parse_charts,
            "dashboards/**/*.yaml": self._parse_dashboard,
            "databases/**/*.yaml": self._parse_database,
            "datasets/**/*.yaml": self._parse_dataset,
        }

        graph = DependencyGraph()
        asset_registry: dict[Asset, AssetData] = {}

        # FIXME: with python 3.14 it should be possible to
        # to switch to ThreadPool for same speedup
        with self.executor(max_workers=os.cpu_count()) as executor:
            futures = [
                executor.submit(func, path)
                for pattern, func in mapping.items()
                for path in folder.glob(pattern)
            ]

            for fut in as_completed(futures):
                chunk = fut.result()
                if chunk is None:
                    continue
                graph.add_asset(chunk.asset, chunk.dependencies, chunk.dependents)

                # Smartly merge asset registry: if we have more metadata for an asset
                # use it
                for key, values in chunk.registry.items():
                    existing = asset_registry.get(key, None)
                    if (
                        existing is None
                        or values.file_path is not None
                        or existing.file_path is None
                    ):
                        asset_registry[key] = values

        graph.enforce_invariants()
        return graph, asset_registry

    def _parse_no_thread(
        self, folder: Path
    ) -> tuple[DependencyGraph, dict[Asset, AssetData]]:
        mapping = {
            "charts/**/*.yaml": self._parse_charts,
            "dashboards/**/*.yaml": self._parse_dashboard,
            "databases/**/*.yaml": self._parse_database,
            "datasets/**/*.yaml": self._parse_dataset,
        }

        graph = DependencyGraph()
        asset_registry: dict[Asset, AssetData] = {}

        for pattern, func in mapping.items():
            for path in folder.glob(pattern):
                chunk = func(path)
                if chunk is None:
                    continue
                graph.add_asset(chunk.asset, chunk.dependencies, chunk.dependents)
                asset_registry.update(chunk.registry)

        graph.enforce_invariants()
        return graph, asset_registry

    def _parse_charts(self, path: Path) -> _AssetChunk | None:
        """Parse a chart file

        Yields [chart, dependencies, dependents]
        """

        chart = self.__load_yaml(path)
        if not (uuid := self.__parse_uuid(chart.get("uuid"))):
            log.warning(f"Chart: f{path} has no uuid. Skipping!")
            return None

        registry = {}
        chart_asset = Asset(uuid=uuid, type=AssetType.CHART)
        registry[chart_asset] = AssetData(
            name=chart["slice_name"],
            file_path=path,
        )

        # Charts has dataset as dependency
        dependencies = set()
        if dataset_uuid := self.__parse_uuid(chart.get("dataset_uuid")):
            dependencies.add(Asset(uuid=dataset_uuid, type=AssetType.DATASET))
            # Datasets have no data here

        return _AssetChunk(
            asset=chart_asset,
            dependencies=dependencies,
            dependents=set(),
            registry=registry,
        )

    def _parse_dashboard(self, path: Path) -> _AssetChunk | None:
        dashboard = self.__load_yaml(path)
        if not (uuid := self.__parse_uuid(dashboard.get("uuid"))):
            log.warning(f"Dashboard: f{path} has no uuid. Skipping!")
            return None

        registry = {}
        dashboard_asset = Asset(uuid=uuid, type=AssetType.DASHBOARD)
        registry[dashboard_asset] = AssetData(
            name=dashboard["dashboard_title"],
            file_path=path,
        )

        dependencies = set()
        if theme_uuid := self.__parse_uuid(dashboard.get("theme_uuid")):
            theme_asset = Asset(uuid=theme_uuid, type=AssetType.THEME)
            dependencies.add(theme_asset)
            # As of superset v6.1, themes are referenced in dashboards but not exported
            # yet. Therefore, register metadata-only placeholder.
            registry[theme_asset] = AssetData(
                name="(theme not exported)",
            )

        for position in dashboard.get("position", {}).values():
            if not isinstance(position, dict):
                continue
            if position.get("type") == "CHART" and (meta := position.get("meta", {})):
                if chart_uuid := self.__parse_uuid(meta.get("uuid")):
                    chart_asset = Asset(chart_uuid, type=AssetType.CHART)
                    if chart_asset not in registry:
                        registry[chart_asset] = AssetData(
                            name=meta["sliceName"],
                        )
                    dependencies.add(chart_asset)

        return _AssetChunk(
            asset=dashboard_asset,
            dependencies=dependencies,
            dependents=set(),
            registry=registry,
        )

    def _parse_database(self, path: Path) -> _AssetChunk | None:
        database = self.__load_yaml(path)

        if not (uuid := self.__parse_uuid(database.get("uuid"))):
            log.warning(f"Database: f{path} has no uuid. Skipping!")
            return None

        registry = {}
        database_asset = Asset(uuid=uuid, type=AssetType.DATABASE)
        registry[database_asset] = AssetData(
            name=database["database_name"],
            file_path=path,
        )

        return _AssetChunk(
            asset=database_asset,
            dependencies=set(),
            dependents=set(),
            registry=registry,
        )

    def _parse_dataset(self, path: Path) -> _AssetChunk | None:
        dataset = self.__load_yaml(path)

        if not (uuid := self.__parse_uuid(dataset.get("uuid"))):
            log.warning(f"Database: f{path} has no uuid. Skipping!")
            return None

        registry = {}
        dataset_asset = Asset(uuid=uuid, type=AssetType.DATASET)
        registry[dataset_asset] = AssetData(
            name=dataset["table_name"],
            file_path=path,
        )
        # Charts has dataset as dependency
        dependencies = set()
        if database_uuid := self.__parse_uuid(dataset.get("database_uuid")):
            dependencies.add(Asset(uuid=database_uuid, type=AssetType.DATABASE))

        return _AssetChunk(
            asset=dataset_asset,
            dependencies=dependencies,
            dependents=set(),
            registry=registry,
        )
