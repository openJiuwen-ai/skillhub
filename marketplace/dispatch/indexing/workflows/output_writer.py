from __future__ import annotations

from pathlib import Path
from typing import Sequence

from indexing.io import write_manifest, write_tree_preset
from indexing.models import CATALOG_FILENAME, TREE_HTML_FILENAME, TREE_INDEX_FILENAME, CatalogRecord
from indexing.tree.visualizer import generate_html as render_tree_html
from indexing.workflows.tree_ops import enrich_branch_descriptions, tree_nodes_to_tree_dict

from .artifacts import write_catalog


def write_index_outputs(
    *,
    output_dir: Path,
    manifest_item_paths: Sequence[str],
    tree_nodes: Sequence[object],
    catalog_records: Sequence[CatalogRecord],
    mode: str,
    item_type: str,
    generate_tree_html: bool,
) -> list[dict[str, object]]:
    enriched_nodes = enrich_branch_descriptions(tree_nodes, catalog_records=catalog_records)
    write_tree_preset({"nodes": enriched_nodes}, output_dir / TREE_INDEX_FILENAME)
    if generate_tree_html:
        render_tree_html(
            tree_nodes_to_tree_dict(enriched_nodes, catalog_records),
            output_dir / TREE_HTML_FILENAME,
        )
    else:
        unlink_if_exists(output_dir / TREE_HTML_FILENAME)
    write_catalog(catalog_records, output_dir / CATALOG_FILENAME)
    write_manifest(output_dir, manifest_item_paths, catalog_records, mode=mode, item_type=item_type)
    return enriched_nodes


def unlink_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()
