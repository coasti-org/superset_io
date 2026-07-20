import io
import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

from superset_io.dependency_graph import Asset, AssetsParser, DependencyGraph
from superset_io.utils import (
    sanitize_assets_bundle,
    validate_assets_bundle_structure,
    zipfile_buffer_from_folder,
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
        selected: list[str] | None = None,
        skip: list[str] | None = None,
        include_dependencies: bool = False,
        overwrite: bool = True,
        sparse: bool = False,
    ):
        """Upload and restore assets from disk.

        Args:
            src_path: Path to a zip file or directory containing assets.
                If directory, must directly contain the metadata.yaml file.
            select: Optional list of asset uuids to upload. If provided, only
                these assets (and optionally their dependencies) will be uploaded.
            skip: Optional list of asset uuids to _not_ upload. Overrules assets found
                via ``select`` and ``include_dependencies``.
            include_dependencies: If True, automatically include all dependencies
                of the selected assets. If False (default), only the explicitly
                selected assets are uploaded, which may result in broken references.
            overwrite: If True (default), overwrite existing assets on the server.
                If False, skip assets that already exist.

        Raises:
            ValueError: If a selected asset is not found in the bundle.

        Note:
            When both ``select`` and ``sparse=True`` are provided, sparse mode
            is automatically enabled regardless of this parameter, since only
            a subset of assets is being uploaded.
        """
        src_path = Path(src_path)

        # Extract zip to temp dir if needed - so parser can work on it
        if src_path.suffix.lower() == ".zip":
            with tempfile.TemporaryDirectory() as tmpdir:
                with zipfile.ZipFile(src_path, "r") as zf:
                    zf.extractall(tmpdir)
                # Find the extracted folder (zip may contain a subfolder)
                extracted = Path(tmpdir)
                contents = list(extracted.iterdir())
                if len(contents) == 1 and contents[0].is_dir():
                    extracted = contents[0]

                src_path = extracted
                return self._upload_from_folder(
                    src_path,
                    selected,
                    skip,
                    include_dependencies,
                    overwrite,
                    sparse,
                )

        return self._upload_from_folder(
            src_path,
            selected,
            skip,
            include_dependencies,
            overwrite,
            sparse,
        )

    def _upload_from_folder(
        self,
        src_path: Path,
        selected: list[str] | None = None,
        skip: list[str] | None = None,
        include_dependencies: bool = False,
        overwrite: bool = True,
        sparse: bool = False,
    ):
        """Upload and restore assets from disk.

        src_path needs to be a directory directly containing the metadata.yml.
        """
        parser = AssetsParser(src_path)
        parser.parse()
        graph = parser.graph
        registry = parser.asset_registry
        zipfile_buffer = None

        # only create a temporary folder if not uploading everything.
        if selected is not None or skip is not None:
            selected_assets = select_assets(
                graph,
                selected,
                skip,
                include_dependencies,
            )
            sparse = True  # Always use sparse when selecting assets

            # Extract selected assets to temp dir
            with tempfile.TemporaryDirectory() as tmpdir:
                tmppath = Path(tmpdir)
                shutil.copyfile(
                    parser.folder / "metadata.yaml", tmppath / "metadata.yaml"
                )
                for asset in selected_assets:
                    asset_data = registry[asset]
                    if asset_data.file_path is not None:
                        dst_file = tmppath / asset_data.file_path.relative_to(
                            parser.folder
                        )
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(asset_data.file_path, dst_file)

                zipfile_buffer = zipfile_buffer_from_folder(tmppath)

        # Create zip buffer from src_path
        if zipfile_buffer is None:
            zipfile_buffer = zipfile_buffer_from_folder(src_path)

        validate_assets_bundle_structure(zipfile_buffer)
        self._import(
            zipfile_buffer=zipfile_buffer,
            sparse=sparse,
            overwrite=overwrite,
        )

    def download(self, dst_path: Path, sanitize: bool = False):
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

        res = self._export()
        # if the zip gets big we might need to consider streaming
        zip_bytes = res.content

        with tempfile.TemporaryDirectory() as _tmpdir:
            # we always unpack, because we want to sanitize
            tmpdir = Path(_tmpdir)
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zip_file:
                zip_file.extractall(tmpdir)

            # get the base folder containing the assets
            _folders = [
                f
                for f in tmpdir.iterdir()
                if f.is_dir() and f.name.startswith("assets_export")
            ]
            if len(_folders) != 1:
                raise ValueError(
                    "Did not find a unique `assets_export` folder in downloaded zip. "
                    "This should not happen."
                )
            assets_folder = _folders[0]

            if sanitize:
                sanitize_assets_bundle(assets_folder)

            if kind == "zip":
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                zip_buffer = zipfile_buffer_from_folder(assets_folder)
                dst_path.write_bytes(zip_buffer.getvalue())
            else:
                for item in assets_folder.iterdir():
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
