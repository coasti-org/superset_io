import logging
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from ruamel.yaml import YAML, YAMLError

from superset_io.api.assets import select_assets
from superset_io.dependency_graph import AssetsParser
from superset_io.dependency_graph.assets import AssetData
from superset_io.dependency_graph.repr import make_console
from superset_io.utils import sanitize_assets_bundle, zipfile_buffer_from_folder

log = logging.getLogger("superset_io")

copy_app = typer.Typer(
    no_args_is_help=True,
    help="Copy or bundle superset assets.",
)


@copy_app.command()
def copy(
    src_path: Annotated[
        Path,
        typer.Argument(
            file_okay=True,
            dir_okay=True,
            exists=True,
            help="Source zip or directory.",
        ),
    ],
    dst_path: Annotated[
        Path,
        typer.Argument(
            file_okay=True,
            dir_okay=True,
            exists=False,
            help="Target zip or directory.",
        ),
    ],
    skip: Annotated[
        list[str] | None,
        typer.Option(
            help="Specify UUIDs of assets exclude from the copy. Can be combined with "
            "--select and gets applied after selection and dependency resolution.",
        ),
    ] = None,
    select: Annotated[
        list[str] | None,
        typer.Option(
            help="Specify UUIDs of assets to copy. If not given, "
            "all assets will be copied. Can be given multiple times.",
        ),
    ] = None,
    include_dependencies: Annotated[
        bool,
        typer.Option(
            help="Whether to include dependencies of selected assets. "
            "Only applies if --select is used. Skipped assets will be removed after.",
        ),
    ] = True,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
    sanitize: Annotated[
        bool,
        typer.Option(
            "--sanitize",
            help="Sanitize copied assets (consistent filenames, smaller YAML).",
        ),
    ] = False,
):
    """Copy assets from source folder to target directory."""

    # Confirm if destination directory already exists and is not empty
    if dst_path.exists() and (
        not dst_path.is_dir() or any(dst_path.iterdir())
    ):
        if not yes and not typer.prompt(
            f"Destination directory '{dst_path}' is not empty. Overwrite?",
            type=bool,
            default=False,
        ):
            typer.echo("Exiting")
            raise typer.Exit(code=1)
        if dst_path.is_dir():
            shutil.rmtree(dst_path)
        else:
            dst_path.unlink()

    # Parse and subselect assets
    parser = AssetsParser(src_path)
    parser.parse()
    graph = parser.graph
    registry = parser.asset_registry

    all_assets = [*graph.assets]
    if not all_assets:
        typer.echo("No assets found. Exiting.", err=True)
        raise typer.Exit(code=1)

    selected_assets = select_assets(
        graph,
        select,
        skip,
        include_dependencies,
    )

    # Perform the copy
    _copy(
        [registry[asset] for asset in selected_assets],
        src_path,
        dst_path,
        sanitize=sanitize,
    )


def _copy(
    assets: list[AssetData],
    source: Path,
    target: Path,
    sanitize: bool = False,
) -> None:
    """Execute the copy operation to the target folder."""

    # Ziping needs a temporary folder, so we just wrap ourself
    if target.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory() as _tmp_dir:
            tmp_dir = Path(_tmp_dir) / "assets_export"
            _copy(assets, source, tmp_dir, sanitize=sanitize)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                zipfile_buffer_from_folder(tmp_dir).getvalue()
            )
        return

    console = make_console()
    console.print(f"[bold]Copying {len(assets)} assets ...")
    console.print(f"from {str(source.absolute())!r}")
    console.print(f"to   {str(target.absolute())!r}")

    _copy_metdata(source, target)

    for asset in assets:
        if not asset.file_path:
            raise FileNotFoundError(f"Asset '{asset.name}' has no file path.")

        relative_path = asset.file_path.relative_to(source.absolute())
        dest_path = target / relative_path

        # Create parent directories if they don't exist
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy the file
        shutil.copy2(asset.file_path, dest_path)
        log.debug(f"Copied {asset.name} to {dest_path}")

    if sanitize:
        sanitize_assets_bundle(target)

    console.print("[bold]Copied successfully!")


def _copy_metdata(source: Path, target: Path):
    metadata_src = source / "metadata.yaml"
    if not metadata_src.exists():
        raise FileNotFoundError(
            f"Metadata file not found at '{metadata_src}'. "
            "Ensure the source directory contains a valid assets bundle."
        )

    # Copy metadata.yaml to target
    metadata_dst = target / "metadata.yaml"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(metadata_src, metadata_dst)

    # Update timestamp in metadata.yaml
    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        metadata_content = yaml.load(metadata_src.read_text())
    except YAMLError as e:
        raise ValueError(f"Failed to parse metadata.yaml: {e}") from e

    if not isinstance(metadata_content, dict):
        raise ValueError("metadata.yaml has invalid format; expected a dictionary.")

    metadata_content["timestamp"] = datetime.now(UTC).isoformat()

    try:
        with metadata_dst.open("w") as f:
            yaml.dump(metadata_content, f)
    except OSError as e:
        raise OSError(f"Failed to write metadata.yaml to '{metadata_dst}': {e}") from e
