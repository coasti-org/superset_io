import io
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap

log = logging.getLogger("superset_io")


def get_version():
    from importlib import metadata

    try:
        return metadata.version("coasti-superset-io")
    except metadata.PackageNotFoundError:
        return "[not found] Use `uv sync` when developing!"


def zipfile_buffer_from_folder(folder: Path | str) -> io.BytesIO:
    """Create a zipfile.ZipFile object from a folder path in memory.
    Args:
        folder (Path | str): Path to the folder to be zipped.
    Returns:
        zipfile.ZipFile: In-memory zipfile object.
    """

    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"Not a folder: {folder}")

    root_prefix = folder.name

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in folder.rglob("*"):
            if path.is_file():
                arcname = Path(root_prefix) / path.relative_to(folder)
                zf.write(path, arcname.as_posix())

    buffer.seek(0)
    return buffer


def zipfile_buffer_from_zipfile(zip_path: Path | str) -> io.BytesIO:
    """Create an in-memory copy of an existing .zip file.
    Args:
        zip_path (Path | str): Path to the existing .zip file.
    Returns:
        io.BytesIO: In-memory buffer containing the zip file contents.
    """
    zip_path = Path(zip_path)
    buffer = io.BytesIO()

    # Read the original zip file and write its contents into the buffer
    with Path(zip_path).open("rb") as f:
        buffer.write(f.read())

    buffer.seek(0)  # Reset buffer pointer to the beginning for reading
    return buffer


def validate_assets_bundle_structure(zip_buffer: io.BytesIO | bytes | Path) -> None:
    """
    Check that a zip files structure will be accepted by /api/v1/assets/import.

    Superset assets bundles typically look like:
      assets_export_<timestamp>/metadata.yaml
      assets_export_<timestamp>/charts/area_54.yaml
      assets_export_<timestamp>/dashboards/world_banks_data_1.yaml
      assets_export_<timestamp>/databases/examples.yaml
      assets_export_<timestamp>/datasets/bart_lines_7.yaml

    IMPORTANT: metadata.yaml is not at the zip root, but under a
    single top-level folder.
    """

    zip_io: io.BytesIO | str
    if isinstance(zip_buffer, (bytes, memoryview, bytearray)):
        zip_io = io.BytesIO(zip_buffer)
    elif isinstance(zip_buffer, Path):
        zip_io = str(zip_buffer)
    else:
        zip_io = zip_buffer

    with zipfile.ZipFile(zip_io, "r") as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]

    try:
        # Find metadata.yaml anywhere
        metadata_candidates = [n for n in names if n.endswith("metadata.yaml")]
        if not metadata_candidates:
            raise ValueError("Missing metadata.yaml in ZIP.")

        # Enforce the expected structure: exactly one top-level folder,
        # and metadata.yaml under it e.g. "assets_export_20260212T092634/metadata.yaml"
        roots = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(roots) != 1:
            raise ValueError(
                "Expected exactly one top-level folder in assets bundle, but found: "
                f"{sorted(roots)}."
            )

        root = next(iter(roots))
        expected_path = f"{root}/metadata.yaml"
        if expected_path not in names:
            raise ValueError(
                f"metadata.yaml not found at expected path '{expected_path}'. "
                f"Candidates were: {metadata_candidates}"
            )
    except ValueError:
        log.debug("Invalid ZIP contents:\n" + "  \n".join(names))
        raise


def sanitize_assets_bundle(folder: Path):
    """Sanitize a folder of assets, so export-import cycles are idempotent."""

    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"Not a folder: {folder}")

    allowed_subfolders = {"charts", "dashboards", "datasets", "databases"}
    tasks: list[tuple[Path, str]] = []

    for file in folder.rglob("*"):
        if not file.is_file():
            continue

        rel_path = file.relative_to(folder)
        if rel_path.parts and rel_path.parts[0] in allowed_subfolders:
            tasks.append((file, rel_path.parts[0]))
        else:
            log.debug(f"Skipping sanitization for {str(file)}")

    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(_sanitize_asset_file, file=file, kind=kind): file
            for file, kind in tasks
        }
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                log.error(f"Failed to process {futures[f]}: {e}")


def _sanitize_asset_file(file: Path, kind: str) -> None:
    """
    Sanitize a single asset file (yaml).

    Renames the file to use its UUID as the filename and strips out some
    unnecessary data.

    Works around a few limitations of the yaml files we get from the superset api:
    - yaml files are named like the asset, and contain an index, which changes often.
      Not ideal for git, so we use a file name that changes less (uuid)
    - yamls contain (cached?) query strings.
      These are not needed for re-upload and bloat the yaml.

    If the YAML has no ``uuid`` field (e.g. a non-asset config file), the file
    is left untouched.
    """

    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        _content = yaml.load(file.read_text(encoding="utf-8"))
        if not isinstance(_content, CommentedMap):
            log.debug(f"Skipping non-dict YAML at {file}")
            return
        content: CommentedMap = _content
    except YAMLError as e:
        raise ValueError(f"Failed to parse yaml: {e}") from e

    if kind == "charts":
        _sanitize_chart_content(content)

    uuid = content.get("uuid")
    if uuid is None:
        output_path = file
        log.debug(f"No UUID in {file}; writing sanitized content in place")
    else:
        new_name = str(uuid).lower() + ".yaml"
        output_path = file.parent / new_name

    # Always (re-) write using ruamel to get stable YAML formatting.
    out = io.StringIO()
    yaml.dump(content, out)
    output_path.write_text(out.getvalue(), encoding="utf-8")

    if output_path != file:
        file.unlink(missing_ok=True)
        log.debug(f"Renamed {file.name} -> {output_path.name}")


def _sanitize_chart_content(content: CommentedMap) -> None:
    """
    Sanitize yaml content for charts.

    Modifes the CommentedMap in-place.
    """
    content.pop("query_context", None)
