from collections import defaultdict
from pathlib import Path
from typing import Annotated, Literal

import typer
from prompt_toolkit.shortcuts import choice

from superset_io.dependency_graph import Asset, AssetsParser, AssetType, DependencyGraph
from superset_io.dependency_graph.assets import AssetData
from superset_io.dependency_graph.repr import print_dep_tree_rich, rich_label

explore_app = typer.Typer(
    no_args_is_help=True,
    help="Explore downloaded superset assets.",
)


class AssetParserContext(typer.Context):
    obj: AssetsParser


@explore_app.callback()
def load_assets(
    ctx: AssetParserContext,
    src_path: Annotated[
        Path,
        typer.Argument(
            file_okay=True,
            dir_okay=True,
            exists=True,
            help="Source zip or directory.",
        ),
    ],
):
    ctx.obj = AssetsParser(src_path)
    ctx.obj.parse()


@explore_app.command(name="list")
def list_(
    ctx: AssetParserContext,
    uuids: Annotated[bool, typer.Option(help="Whether to show UUIDs.")] = True,
):
    """List assets from a downloaded assets folder or zip."""
    graph = ctx.obj.graph
    registry = ctx.obj.asset_registry

    grouped: dict[AssetType, set[Asset]] = defaultdict(set)
    for asset in graph.assets:
        grouped[asset.type].add(asset)

    for asset_type, assets in grouped.items():
        print(f"{asset_type.name}:")
        for asset in assets:
            if uuids:
                print(f"  {registry[asset].name} ({asset.uuid})")
            else:
                print(f"  {registry[asset].name}")


@explore_app.command()
def graph(
    ctx: AssetParserContext,
    asset: Annotated[
        str | None,
        typer.Option(help="UUID of asset. If not given, will be prompted."),
    ] = None,
    direction: Annotated[
        Literal["upstream", "downstream"] | None, typer.Option(help="")
    ] = None,
    uuids: Annotated[bool, typer.Option(help="Whether to show UUIDs.")] = True,
):
    """Dependency graph of a specific asset."""
    asset_ = prompt_for_asset(
        ctx.obj.graph,
        ctx.obj.asset_registry,
        asset,
    )

    if direction is None:
        direction = choice(
            message="Explore direction:",
            options=[
                ("upstream", "Dependencies (what this asset relies on)"),
                ("downstream", "Dependents (what relies on this asset)"),
            ],
            default="upstream",
        )

    print_dep_tree_rich(
        ctx.obj.graph,
        root=asset_,
        label=lambda a: rich_label(a, ctx.obj.asset_registry, uuids),
        direction=direction,  # type: ignore[arg-type]
    )


def prompt_for_asset(
    g: DependencyGraph,
    registry: dict[Asset, AssetData],
    asset: str | None = None,
) -> Asset:
    # Prompt for assets
    if asset is None:
        print(" Select asset:")
        asset_type = choice(
            message=" Asset type:",
            options=[(a, a.name) for a in AssetType],
            default=AssetType.DATABASE,
        )

        assets = [*g.assets_of_type(asset_type)]
        if len(assets) == 0:
            raise Exception(f"No assets of type {asset_type} found.")

        return choice(
            message=f" {asset_type.name.lower().capitalize()}s:",
            options=[(a, registry[a].name) for a in assets],
            default=assets[0],
        )
    else:
        asset_ = g.get_asset(asset)
        if not asset_:
            raise Exception(f"Asset {asset} not found!")
        return asset_
