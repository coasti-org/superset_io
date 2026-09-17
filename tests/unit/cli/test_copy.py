import shutil
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from superset_io.cli.copy import copy_app

SAMPLE_ASSETS = Path(__file__).parents[2] / "assets" / "sample_assets"
DASHBOARD_UUID = "00000000-0000-0000-0000-da54b0aad000"


def _create_assets_zip(destination: Path) -> Path:
    with zipfile.ZipFile(destination, "w") as archive:
        for source_file in SAMPLE_ASSETS.rglob("*"):
            if source_file.is_file():
                archive.write(
                    source_file,
                    source_file.relative_to(SAMPLE_ASSETS.parent).as_posix(),
                )
    return destination


def _run_copy(*arguments: Path | str) -> None:
    result = CliRunner().invoke(
        copy_app,
        [*(str(argument) for argument in arguments), "--yes"],
    )
    assert result.exit_code == 0, result.stdout


def test_copy_directory_to_directory(tmp_path: Path) -> None:
    destination = tmp_path / "copied"

    _run_copy(SAMPLE_ASSETS, destination)

    assert (destination / "metadata.yaml").exists()
    assert (destination / "dashboards" / "Test_Dash_1.yaml").exists()
    assert (destination / "charts" / "Age_1.yaml").exists()
    assert (destination / "databases" / "SQLite.yaml").exists()
    assert (destination / "datasets" / "SQLite" / "people_1.yaml").exists()


def test_copy_accepts_zip_source(tmp_path: Path) -> None:
    source_zip = _create_assets_zip(tmp_path / "assets.zip")
    destination = tmp_path / "copied"

    _run_copy(source_zip, destination)

    assert (destination / "metadata.yaml").exists()
    assert (destination / "dashboards" / "Test_Dash_1.yaml").exists()
    assert (destination / "databases" / "SQLite.yaml").exists()
    assert (destination / "datasets" / "SQLite" / "people_1.yaml").exists()


def test_copy_creates_assets_zip(tmp_path: Path) -> None:
    destination = tmp_path / "copied.zip"

    _run_copy(SAMPLE_ASSETS, destination)

    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())

    assert "assets_export/metadata.yaml" in names
    assert "assets_export/dashboards/Test_Dash_1.yaml" in names
    assert "assets_export/databases/SQLite.yaml" in names
    assert "assets_export/datasets/SQLite/people_1.yaml" in names


def test_full_copy_preserves_tags_yaml(tmp_path: Path) -> None:
    """Full copies keep non-asset root files such as tags.yaml.

    Superset exports place a tags.yaml next to metadata.yaml. A full copy
    represents the whole bundle, so dropping tags.yaml would silently lose
    tags on export -> sanitize -> import round trips.
    """
    source = tmp_path / "source"
    shutil.copytree(SAMPLE_ASSETS, source)
    tags_content = "tags:\n- tag_name: Test\n  description: asdsadas\n"
    (source / "tags.yaml").write_text(
        tags_content,
        encoding="utf-8",
        newline="\n",  # avoid windows converting to crlf
    )

    # Directory target
    destination_dir = tmp_path / "copied"
    _run_copy(source, destination_dir)
    assert (destination_dir / "tags.yaml").read_text(encoding="utf-8") == tags_content

    # Zip target (goes through the same _copy recursion)
    destination_zip = tmp_path / "copied.zip"
    _run_copy(source, destination_zip)
    with zipfile.ZipFile(destination_zip) as archive:
        assert archive.read("assets_export/tags.yaml").decode() == tags_content


def test_copy_selects_assets_without_dependencies(tmp_path: Path) -> None:
    destination = tmp_path / "copied"
    result = CliRunner().invoke(
        copy_app,
        [
            str(SAMPLE_ASSETS),
            str(destination),
            "--yes",
            "--select",
            DASHBOARD_UUID,
            "--no-include-dependencies",
        ],
    )
    assert result.exit_code == 0, result.stdout

    assert (destination / "dashboards" / "Test_Dash_1.yaml").exists()
    assert not any((destination / "charts").glob("*.yaml"))
    assert not any((destination / "datasets").glob("*.yaml"))
    assert not any((destination / "databases").glob("*.yaml"))


# -------------------------------- Sanitzation ------------------------------- #


def test_copy_sanitizes_directory_output(tmp_path: Path) -> None:
    destination = tmp_path / "copied"
    result = CliRunner().invoke(
        copy_app,
        [str(SAMPLE_ASSETS), str(destination), "--yes", "--sanitize"],
    )
    assert result.exit_code == 0, result.stdout

    sanitized_chart = (
        destination / "charts" / "c1a87000-0000-0000-0000-000000000000.yaml"
    )
    assert sanitized_chart.exists()
    assert not (destination / "charts" / "Age_1.yaml").exists()
    assert "query_context:" not in sanitized_chart.read_text(encoding="utf-8")
    assert (
        destination / "dashboards" / "00000000-0000-0000-0000-da54b0aad000.yaml"
    ).exists()
    assert (
        destination / "databases" / "00000000-da7a-ba5e-0000-000000000000.yaml"
    ).exists()
    assert (
        destination
        / "datasets"
        / "SQLite"
        / "00000000-da7a-5e70-0000-000000000000.yaml"
    ).exists()


def test_copy_sanitizes_zip_output(tmp_path: Path) -> None:
    destination = tmp_path / "copied.zip"

    result = CliRunner().invoke(
        copy_app,
        [str(SAMPLE_ASSETS), str(destination), "--yes", "--sanitize"],
    )
    assert result.exit_code == 0, result.stdout

    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        sanitized_chart = archive.read(
            "assets_export/charts/c1a87000-0000-0000-0000-000000000000.yaml"
        ).decode()

    assert "assets_export/charts/Age_1.yaml" not in names
    assert "assets_export/dashboards/00000000-0000-0000-0000-da54b0aad000.yaml" in names
    assert "assets_export/databases/00000000-da7a-ba5e-0000-000000000000.yaml" in names
    assert (
        "assets_export/datasets/SQLite/00000000-da7a-5e70-0000-000000000000.yaml"
        in names
    )
    assert "query_context:" not in sanitized_chart
