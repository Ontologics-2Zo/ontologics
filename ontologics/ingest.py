"""Deterministic, read-only ingestion of the pinned OLIMEX ODS BOMs.

The importer prepares candidate graph nodes. It does not approve or persist them.
ODS cells and source hashes remain the evidence; component keys are source scoped.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
TABLE = "{" + NS["table"] + "}"
MAX_SOURCE_BYTES = 5_000_000
MAX_XML_BYTES = 10_000_000


def parse_olimex_ods(path: str | Path) -> list[dict]:
    """Read OLIMEX Quantity/Designation/Package/Designator ODS tables.

    Repeated cells are expanded (the switch/connector rows use this feature).
    File and XML sizes are bounded; formulas, duplicate positions, missing values,
    invalid quantities and quantity/position disagreement fail closed.
    """
    path = Path(path)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("SOURCE_TOO_LARGE: expected a small ODS BOM")
    with zipfile.ZipFile(path) as archive:
        if archive.getinfo("content.xml").file_size > MAX_XML_BYTES:
            raise ValueError("SOURCE_TOO_LARGE: expanded XML")
        xml = archive.read("content.xml")
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ValueError("UNSUPPORTED_XML: external entities are forbidden")
    root = ET.fromstring(xml)
    tables = root.findall(".//table:table", NS)
    if len(tables) != 1:
        raise ValueError("AMBIGUOUS_SHEETS: expected one BOM sheet")
    table = tables[0]
    sheet = table.get(TABLE + "name")
    rows, seen, header = [], set(), False
    physical_row = 0
    for row in table.findall("table:table-row", NS):
        repetitions = int(row.get(TABLE + "number-rows-repeated", "1"))
        if repetitions < 1:
            raise ValueError("INVALID_ROW_REPEAT")
        physical_row += 1
        values = []
        for cell in list(row):
            if cell.tag not in (TABLE + "table-cell", TABLE + "covered-table-cell"):
                continue
            if cell.get(TABLE + "formula"):
                raise ValueError("UNSUPPORTED_FORMULA: BOM source must contain explicit values")
            repeat = int(cell.get(TABLE + "number-columns-repeated", "1"))
            if repeat < 1:
                raise ValueError("INVALID_CELL_REPEAT")
            value = " ".join("".join(p.itertext()) for p in cell.findall(".//text:p", NS)).strip()
            # Blank padding may repeat 1020 times; only four business columns exist.
            values.extend([value] * min(repeat, max(0, 4 - len(values))))
            if len(values) == 4:
                break
        values += [""] * (4 - len(values))
        if not any(values):
            physical_row += repetitions - 1
            continue
        if repetitions != 1:
            raise ValueError("REPEATED_DATA_ROWS: explicit review needed")
        if values == ["Quantity", "Designation", "Package", "Designator"]:
            if header:
                raise ValueError("DUPLICATE_HEADER")
            header = True
            continue
        if not header:
            raise ValueError("UNKNOWN_HEADER: cannot map source columns")
        qty, designation, package, designators = values
        if not qty.isdigit() or int(qty) <= 0 or not all(values):
            raise ValueError(f"INVALID_BOM_ROW: row {physical_row}")
        positions = [p.strip() for p in designators.split(",")]
        if not all(positions) or len(positions) != int(qty):
            raise ValueError(f"QUANTITY_MISMATCH: row {physical_row}")
        if len(set(positions)) != len(positions) or seen.intersection(positions):
            raise ValueError(f"DUPLICATE_POSITION: row {physical_row}")
        seen.update(positions)
        condition = "unknown" if designation.startswith(("NA", "NS(")) or designation == "Opened" else "populated"
        rows.append({
            "sheet": sheet, "row": physical_row,
            "locator": f"'{sheet}'!A{physical_row}:D{physical_row}",
            "quantity": int(qty), "raw_value": designation, "package": package,
            "positions": positions, "population": condition,
        })
    if not rows:
        raise ValueError("EMPTY_BOM")
    return rows


def build_catalog(root: str | Path | None = None) -> dict:
    """Verify raw source hashes and build the versioned real-data graph candidates."""
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "data/sources/manifest.json").read_text())
    sources = manifest["sources"]
    for source in sources:
        local = (root / source["path"]).resolve()
        if not local.is_relative_to(root.resolve()):
            raise ValueError("SOURCE_PATH_ESCAPE")
        if hashlib.sha256(local.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"SOURCE_HASH_MISMATCH: {source['id']}")
    nodes = list(sources)
    summary = []
    model_id = "model:olimex:esp32-poe"
    model_evidence = "evidence:olimex:product-family"
    nodes += [{
        "id": model_evidence, "type": "Evidence", "origin": "real", "evidence": [],
        "source_id": "source:olimex-product-page",
        "locator": "h1 ESP32-POE; FEATURES; DOCUMENTS; HARDWARE",
        "description": "Official product family page links the OLIMEX ESP32-POE hardware repository. Current page is not evidence of every historical revision's specifications.",
    }, {
        "id": model_id, "type": "ProductModel", "origin": "real", "evidence": [model_evidence],
        "name": "ESP32-POE", "manufacturer": "OLIMEX",
        "specifications": {
            "scope": "current product-family page only; not automatically applied to historical revisions",
            "ethernet_interface": "100Mb",
            "pcb_dimensions_mm": [80, 28],
            "features": ["WiFi", "Bluetooth", "LiPo battery charger", "MicroSD card", "UEXT connector"],
            "evidence": [model_evidence],
        },
    }]
    for source in sources:
        if source.get("kind") != "bom":
            continue
        source_id, revision = source["id"], source["revision"]
        rows = parse_olimex_ods(root / source["path"])
        revision_id = f"revision:esp32-poe:{revision}"
        config_id = f"config:esp32-poe:{revision}:WROOM"
        identity_evidence = f"evidence:olimex-{revision}-wroom:identity"
        nodes.append({
            "id": identity_evidence, "type": "Evidence", "origin": "real", "evidence": [],
            "source_id": source_id + "-pdf", "locator": "pages 1-2 header; page 2 U6 row",
            "description": f"PDF header ESP32-PoE_Rev_{revision}-BOM(WROOM); U6 designation ESP32-WROOM-32E. ODS sheet label differs and is retained as metadata_warning.",
        })
        nodes.extend([
            {"id": revision_id, "type": "ProductRevision", "origin": "real", "evidence": [identity_evidence], "model_id": model_id, "revision": revision},
            {"id": config_id, "type": "Configuration", "origin": "real", "evidence": [identity_evidence],
             "revision_id": revision_id, "variant": "WROOM", "name": f"ESP32-POE Rev {revision} WROOM",
             "identity_notes": source["metadata_warning"],
             "population_notes": "NA, NS and Opened raw labels are preserved as unknown; source legend/assembly-policy confirmation required before procurement calculations."},
        ])
        for row in rows:
            ev_id = f"evidence:olimex-{revision}-wroom:row-{row['row']}"
            identity = source_id + "|" + row["raw_value"] + "|" + row["package"]
            component_id = f"component:olimex-{revision}-wroom:" + hashlib.sha256(identity.encode()).hexdigest()[:16]
            nodes.extend([
                {"id": ev_id, "type": "Evidence", "origin": "real", "evidence": [],
                 "source_id": source_id, "locator": row["locator"],
                 "description": f"Quantity={row['quantity']}; Designation={row['raw_value']}; Package={row['package']}; Designator={','.join(row['positions'])}"},
                {"id": component_id, "type": "ComponentModel", "origin": "real", "evidence": [ev_id],
                 "name": row["raw_value"], "identity_key": identity, "package": row["package"],
                 "identity_scope": "source designation and package, not verified global manufacturer part number"},
                {"id": f"bom:esp32-poe:{revision}:WROOM:row-{row['row']}", "type": "BOMItem", "origin": "real", "evidence": [ev_id],
                 "configuration_id": config_id, "component_id": component_id, "quantity": row["quantity"], "unit": "piece",
                 "positions": row["positions"], "population": row["population"], "raw_value": row["raw_value"]},
            ])
        summary.append({
            "configuration_id": config_id, "bom_rows": len(rows), "design_positions": sum(r["quantity"] for r in rows),
            "unknown_population_rows": sum(r["population"] == "unknown" for r in rows),
            "checks": ["source_sha256", "exact_header", "positive_integer_quantity", "quantity_equals_position_count", "unique_positions", "PDF_identity_header_and_U6_visual_check"],
            "limitations": ["NA/NS/Opened population semantics not confirmed", "ODS sheet metadata conflict retained", "No global component identity or compatibility inferred", "Not every BOM row visually checked against PDF"],
        })
    return {"contract_version": "0.1.0", "nodes": nodes, "verification": summary}


if __name__ == "__main__":
    catalog = build_catalog()
    destination = Path(__file__).resolve().parents[1] / "data/catalog.json"
    destination.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"path": str(destination), "nodes": len(catalog["nodes"]), "verification": catalog["verification"]}, ensure_ascii=False, indent=2))
