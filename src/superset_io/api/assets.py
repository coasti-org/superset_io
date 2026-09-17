import io
import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

from superset_io.dependency_graph import Asset, DependencyGraph
from superset_io.utils import (
    validate_assets_bundle_structure,
    zipfile_buffer_from_folder,
    zipfile_buffer_from_zipfile,
)

from .abc import ClientBase

log = logging.getLogger("superset_io")


class AssetsApiClient(ClientBase):
    # See also https://superset.apache.org/developer-docs/api/export-all-assets

    def _import(
        self,
        zipfile_buffer: io.BytesIO | Path | bytes,
        overwrite: bool = False,
        passwords: dict[str, str] | None = None,
        ssh_tunnel_passwords: dict[str, str] | None = None,
        ssh_tunnel_private_key_passwords: dict[str, str] | None = None,
        ssh_tunnel_private_keys: dict[str, str] | None = None,
        sparse: bool = False,
    ):
        """Import multiple assets"""

        # Get the zip content
        if isinstance(zipfile_buffer, io.BytesIO):
            zip_content = zipfile_buffer.getvalue()
        elif isinstance(zipfile_buffer, Path):
            zip_content = zipfile_buffer.read_bytes()
        elif isinstance(zipfile_buffer, bytes):
            zip_content = zipfile_buffer
        else:
            raise ValueError("zipfile must be io.BytesIO, Path, or bytes")

        # only the file goes into `files=`
        files = {
            "bundle": (
                "name_does_not_matter.zip",
                zip_content,
                "application/zip",
            )
        }

        # all non-file fields go into `data=`
        data = {
            "passwords": json.dumps(passwords or {}),
            "ssh_tunnel_passwords": json.dumps(ssh_tunnel_passwords or {}),
            "ssh_tunnel_private_keys": json.dumps(ssh_tunnel_private_keys or {}),
            "ssh_tunnel_private_key_passwords": json.dumps(
                ssh_tunnel_private_key_passwords or {}
            ),
            "sparse": sparse,
        }
        if overwrite:
            data["overwrite"] = "true"

        # ensure content-type is not set, to allow requests.post to set it.
        # this is needed so the boundary (file length) is also set automatically
        headers = dict(self.session.headers)
        headers.pop("Content-Type", None)
        headers["Referer"] = self.session.base_url.rstrip("/") + "/"

        res = self.session.post(
            "/api/v1/assets/import/",
            files=files,
            data=data,
            headers=headers,
        )

        res.raise_for_status()

        return res

    def _export(self):
        """Gets a ZIP file with all the Superset assets.

        Includes databases, datasets, charts, dashboards, saved queries
        as YAML files.
        """
        url = f"{self.session.base_url}/api/v1/assets/export"
        res = self.session.get(url)
        res.raise_for_status()
        return res

    def upload(
        self,
        src_path: Path,
        overwrite: bool = True,
        sparse: bool = False,
    ):
        """Upload and restore assets from disk.

        Args:
            src_path: Path to a zip file or directory containing assets.
                If directory, must directly contain the metadata.yaml file.
                If zip file, expects the format you get from superset:
                Exactly one contained top-level folder that holds the metadata.yaml.
            overwrite: If True (default), overwrite existing assets on the server.
                If False, skip assets that already exist.
            sparse: Needs to be set to true when only a subset of assets is selected.
                This will tell the superset api to skip dependency checks.

        Raises:
            ValueError: If a selected asset is not found in the bundle.

        Note:
            When both ``select`` and ``sparse=True`` are provided, sparse mode
            is automatically enabled regardless of this parameter, since only
            a subset of assets is being uploaded.
        """

        if src_path.is_dir():
            zipfile_buffer = zipfile_buffer_from_folder(src_path)
        else:
            zipfile_buffer = zipfile_buffer_from_zipfile(src_path)

        validate_assets_bundle_structure(zipfile_buffer)

        self._import(
            zipfile_buffer=zipfile_buffer,
            sparse=sparse,
            overwrite=overwrite,
        )

    def download(self, dst_path: Path):
        """Download and export all assets to disk.

        Depending an provided dst_path, we either write as zip file or the extracted
        folder structure.
        """

        if dst_path.suffix.lower() == ".zip":
            kind = "zip"
        else:
            kind = "folder"
            dst_path.mkdir(parents=True, exist_ok=True)

        if kind == "folder" and any(dst_path.iterdir()):
            raise ValueError(f"Destination directory '{dst_path}' is not empty")

        # if the zip gets big we might need to consider streaming
        res = self._export()
        zip_bytes = res.content

        if kind == "zip":
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            dst_path.write_bytes(zip_bytes)
        else:
            # Extract the zip to temp dir, get the assets and move to dst_path.
            # This is now the only point where we do zip -> folder conversion.
            # If we need this elsewhere, move it into a helper!
            with tempfile.TemporaryDirectory() as _tmp_dir:
                tmp_dir = Path(_tmp_dir)
                zip_file = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
                zip_file.extractall(tmp_dir)

                src_folders = [
                    f for f in tmp_dir.iterdir() if f.name.startswith("assets_export")
                ]
                if len(src_folders) != 1:
                    raise ValueError(
                        "Did not find a unique `assets_export` folder in downloaded "
                        "zip. This should not happen."
                    )

                for item in src_folders[0].iterdir():
                    shutil.move(item, dst_path / item.name)


def select_assets(
    graph: DependencyGraph,
    selected: list[str] | None = None,
    skip: list[str] | None = None,
    include_dependencies: bool = False,
) -> set[Asset]:
    """Select a subset of assets based on criteria.

    Args:
        graph: The dependency graph containing assets.
        selected: Optional list of asset UUIDs to include. If None, all assets
            are selected.
        skip: Optional list of asset UUIDs to exclude. Applied after selection.
        include_dependencies: If True, include all upstream dependencies of
            selected assets.

    Returns:
        A set of selected Asset objects.

    Raises:
        ValueError: If a selected asset is not found in the graph.
    """
    if selected is None:
        selected_assets: set[Asset] = set(graph.assets)
    else:
        selected_assets = set()
        for sel in selected:
            asset = graph.get_asset(sel)
            if asset is None:
                raise ValueError(f"Asset {sel!r} not found in graph!")
            selected_assets.add(asset)

        # Include dependencies if requested (only makes sense when selecting)
        if include_dependencies:
            to_visit = list(selected_assets)
            while to_visit:
                current = to_visit.pop()
                for dep in graph.get_dependencies(current):
                    if dep not in selected_assets:
                        selected_assets.add(dep)
                        to_visit.append(dep)

    # Apply negative selection (skip)
    if skip:
        for sel in skip:
            asset = graph.get_asset(sel)
            if asset is None:
                raise ValueError(f"Asset to skip {sel!r} not found in graph!")
            selected_assets.discard(asset)

    return selected_assets
