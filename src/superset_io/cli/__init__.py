import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import requests
import typer
from dotenv import load_dotenv

from superset_io.api import SupersetApiClient, SupersetApiSession
from superset_io.utils import (
    get_version,
    sanitize_assets_bundle,
    zipfile_buffer_from_folder,
)

from .copy import copy_app
from .explore import explore_app
from .utils import catch_exception

# Load env vars also from .env
load_dotenv()

log = logging.getLogger("superset_io")
logging.basicConfig(level="INFO")
app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    add_completion=False,
)
app.add_typer(explore_app, name="explore")
app.add_typer(copy_app)


class ApiClientContext(typer.Context):
    obj: SupersetApiClient


@app.callback()
def main(
    ctx: typer.Context,
    base_url: Annotated[
        str | None,
        typer.Option(
            help="Base URL for Superset instance",
            envvar="SUPERSET_BASE_URL",
        ),
    ] = None,
    username: Annotated[
        str | None,
        typer.Option(
            help="Username for Superset user",
            envvar="SUPERSET_USER",
        ),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option(
            help="Password for Superset user",
            envvar="SUPERSET_PASSWORD",
        ),
    ] = None,
    access_token: Annotated[
        str | None,
        typer.Option(
            help="Access token as alternative to user and password",
            hide_input=True,
            envvar="SUPERSET_ACCESS_TOKEN",
        ),
    ] = None,
) -> None:
    r"""
    Superset-IO

    Automate import, export, and exploration of Superset assets
    via the Superset REST API.

    © coasti
    """

    # these subcommands do not need online access, and no authentication
    if ctx.invoked_subcommand in ["explore", "version", "copy"]:
        return

    # help output should not trigger a login attempt
    if "--help" in sys.argv[1:]:
        return

    if not isinstance(ctx.obj, ApiClientContext):
        ctx.obj = authenticate(
            base_url,
            username,
            password,
            access_token,
        )


@catch_exception(
    exception=requests.ConnectionError,
    exit_code=1,
)
def authenticate(
    base_url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    access_token: str | None = None,
) -> SupersetApiClient:
    """
    Initialize the global superset_api by asking users to input their credentials.

    Runs before each api call.

    helpful:
    https://stackoverflow.com/questions/68646596/how-to-get-superset-token-for-use-rest-api
    """

    if base_url is None:
        base_url = str(typer.prompt("URL", type=str, default="http://localhost:8088"))
    else:
        log.info(f"Connecting to Superset at: {base_url}")

    if access_token is None:
        if (
            password is None
            and (password_file := os.environ.get("SUPERSET_PASSWORD_FILE")) is not None
        ):
            password = Path(password_file).read_text().rstrip()

        session = SupersetApiSession.from_credentials(
            base_url=base_url,
            username=username
            or typer.prompt("Username", type=str, hide_input=False, default="admin"),
            password=password
            or typer.prompt("Password", type=str, hide_input=True, default="admin"),
        )
    else:
        log.debug("Authenticating using access token")
        session = SupersetApiSession.from_token(
            base_url=base_url,
            bearer_token=access_token,
        )

    return SupersetApiClient(session)


@app.command()
def version():
    """Shows version and exit."""
    log.info(f"coasti-superset-io version {get_version()}")


@app.command()
@catch_exception(
    exception=requests.ConnectionError,
    exit_code=1,
)
def test(
    ctx: ApiClientContext,
):
    """Test the connection to configured superset instance."""

    ctx.obj.test_connection()


@app.command()
def download(
    ctx: ApiClientContext,
    dst_path: Annotated[
        Path,
        typer.Argument(
            file_okay=True,
            dir_okay=True,
            help="Destination zip or directory.",
        ),
    ],
    sanitize: Annotated[
        bool,
        typer.Option(
            "--sanitize",
            help="Sanitize downloaded assets (consistent filenames, smaller YAML).",
        ),
    ] = False,
):
    """Download all assets from server to zip or yaml directory."""

    if dst_path.is_dir() and any(dst_path.iterdir()):
        if typer.prompt(
            f"Destination directory '{dst_path}' is not empty. Delete and re-use?",
            type=bool,
            default=False,
        ):
            shutil.rmtree(dst_path)
        else:
            log.info("Exiting")
            raise typer.Exit(code=1)
    if not sanitize:
        ctx.obj.assets.download(dst_path)
        return

    # We want to keep sanitization out of the api layer. Do it at cli level.
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_path = Path(temporary_directory) / "assets_export"
        ctx.obj.assets.download(temporary_path)
        sanitize_assets_bundle(temporary_path)

        if dst_path.suffix.lower() == ".zip":
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            zip_buffer = zipfile_buffer_from_folder(temporary_path)
            dst_path.write_bytes(zip_buffer.getvalue())
        else:
            dst_path.mkdir(parents=True, exist_ok=True)
            for item in temporary_path.iterdir():
                shutil.move(item, dst_path / item.name)


@app.command()
def upload(
    ctx: ApiClientContext,
    src_path: Annotated[
        Path,
        typer.Argument(
            file_okay=True,
            dir_okay=True,
            exists=True,
            help="Source zip or directory.",
        ),
    ],
    skip: Annotated[
        list[str] | None,
        typer.Option(
            help="Specify UUIDs of assets exclude from upload. Can be combined with "
            "--select and gets applied after selection and dependency resolution.",
        ),
    ] = None,
    select: Annotated[
        list[str] | None,
        typer.Option(
            help="Specify UUIDs of assets to upload. If not given, "
            "all assets will be uploaded. Can be given multiple times.",
        ),
    ] = None,
    include_dependencies: Annotated[
        bool,
        typer.Option(
            help="Whether to include dependencies of selected assets. "
            "Only applies if --select is used. Assets given --skip are removed at the "
            "very end (after resolving dependencies).",
        ),
    ] = True,
    force: Annotated[
        bool,
        typer.Option(
            help="Skip confirmation before overwriting remote assets.",
        ),
    ] = False,
):
    """Upload all assets from zip or yaml directory to server."""

    if not force and not typer.confirm(
        f"This will overwrite content on {ctx.obj.session.base_url} and "
        "CANNOT BE UNDONE.\nProceed?",
        default=False,
    ):
        log.info("Exiting")
        raise typer.Exit(code=1)

    ctx.obj.assets.upload(
        src_path,
        selected=select,
        skip=skip,
        include_dependencies=include_dependencies,
    )
