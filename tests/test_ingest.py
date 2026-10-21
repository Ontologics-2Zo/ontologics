import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ontologics.ingest import build_catalog, parse_olimex_ods


ROOT = Path(__file__).resolve().parents[1]


class IngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = build_catalog(ROOT)
        cls.nodes = {node["id"]: node for node in cls.catalog["nodes"]}
        cls.rows = {
            revision: parse_olimex_ods(ROOT / f"data/sources/ESP32-PoE_Rev_{revision}-BOM-WROOM.ods")
            for revision in ("L", "L1")
        }

    def test_raw_files_match_sha256_and_official_git_blobs(self):
        expected = {
            "source:olimex-L-wroom": "0a9947073730d5afe5cfeef24ed11d564904d133",
            "source:olimex-L1-wroom": "e1f69232695ec33b0659cbe2d47979cbe031cf10",
            "source:olimex-L-wroom-pdf": "3110f86a4ea4d978ca32019eeaa8478f53965dae",
            "source:olimex-L1-wroom-pdf": "7b03a0136979f725eff35c18c990606001f1e462",
        }
        for node in self.nodes.values():
            if node["type"] != "SourceDocument":
                continue
            with self.subTest(source=node["id"]):
                raw = (ROOT / node["path"]).read_bytes()
                self.assertEqual(node["sha256"], hashlib.sha256(raw).hexdigest())
                if node["id"] in expected:
                    git_sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                    self.assertEqual(expected[node["id"]], git_sha)

    def test_both_complete_boms_have_74_rows_and_128_unique_positions(self):
        for revision, rows in self.rows.items():
            with self.subTest(revision=revision):
                self.assertEqual(74, len(rows))
                self.assertEqual(128, sum(row["quantity"] for row in rows))
                positions = [position for row in rows for position in row["positions"]]
                self.assertEqual(128, len(set(positions)))
                for row in rows:
                    self.assertEqual(row["quantity"], len(row["positions"]))

    def test_exact_lan_rows_retain_cell_provenance(self):
        for revision, row_number, digest in (("L", 55, "32d420c6c802a2c2"), ("L1", 54, "13b3a09341fd15ef")):
            component = self.nodes[f"component:olimex-{revision}-wroom:{digest}"]
            bom = self.nodes[f"bom:esp32-poe:{revision}:WROOM:row-{row_number}"]
            evidence = self.nodes[bom["evidence"][0]]
            self.assertEqual("LAN8710A-EZC(QFN32)", component["name"])
            self.assertEqual(["U4"], bom["positions"])
            self.assertEqual(1, bom["quantity"])
            self.assertEqual(f"source:olimex-{revision}-wroom", evidence["source_id"])
            self.assertTrue(evidence["locator"].endswith(f"!A{row_number}:D{row_number}"))

    def test_uncertain_population_is_never_assumed_populated(self):
        for revision, expected_unknown in (("L", 11), ("L1", 12)):
            rows = self.rows[revision]
            uncertain = [row for row in rows if row["raw_value"].startswith(("NA", "NS(")) or row["raw_value"] == "Opened"]
            self.assertEqual(expected_unknown, len(uncertain))
            self.assertTrue(all(row["population"] == "unknown" for row in uncertain))

    def test_variant_identity_links_pdf_and_retains_sheet_conflict(self):
        for revision in ("L", "L1"):
            config = self.nodes[f"config:esp32-poe:{revision}:WROOM"]
            identity = self.nodes[config["evidence"][0]]
            self.assertEqual(f"source:olimex-{revision}-wroom-pdf", identity["source_id"])
            self.assertIn("header", identity["locator"])
            self.assertIn("U6", identity["locator"])
            self.assertIn("ODS sheet name conflicts", config["identity_notes"])
            self.assertIn("WROVER", self.rows[revision][0]["sheet"])

    def test_source_scoped_models_do_not_merge_same_name_across_revisions(self):
        components = [n for n in self.nodes.values() if n["type"] == "ComponentModel" and n["name"] == "LAN8710A-EZC(QFN32)"]
        self.assertEqual(2, len(components))
        self.assertEqual(2, len({n["id"] for n in components}))
        self.assertEqual(2, len({n["identity_key"] for n in components}))

    def test_repeated_cells_preserve_package_and_position(self):
        # Original ODS repeats B and C for these parts; treating them as one cell
        # would shift the position into Package and lose the BOM designators.
        for rows in self.rows.values():
            button = next(row for row in rows if row["raw_value"] == "IT1185AU2")
            battery = next(row for row in rows if row["raw_value"] == "DW02S")
            self.assertEqual("IT1185AU2", button["package"])
            self.assertEqual(["BUT1", "RST1"], button["positions"])
            self.assertEqual("DW02S", battery["package"])
            self.assertEqual(["BAT1"], battery["positions"])

    def test_corrupted_quantity_or_duplicate_position_is_rejected(self):
        with zipfile.ZipFile(ROOT / "data/sources/ESP32-PoE_Rev_L-BOM-WROOM.ods") as source:
            xml = source.read("content.xml")
        cases = [
            (b"R10,R11,R12,R13", b"R10,R11,R12", "QUANTITY_MISMATCH"),
            (b"R10,R11,R12,R13", b"R10,R11,R12,R12", "DUPLICATE_POSITION"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.ods"
            for old, new, error in cases:
                with self.subTest(error=error):
                    self.assertIn(old, xml)
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr("content.xml", xml.replace(old, new))
                    with self.assertRaisesRegex(ValueError, error):
                        parse_olimex_ods(path)

    def test_catalog_is_reproducible_and_source_mutation_is_rejected(self):
        saved = json.loads((ROOT / "data/catalog.json").read_text())
        self.assertEqual(saved, self.catalog)
        self.assertEqual(len(self.catalog["nodes"]), len(self.nodes))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/sources").mkdir(parents=True)
            manifest = copy.deepcopy(json.loads((ROOT / "data/sources/manifest.json").read_text()))
            # Only the first entry needs copying: failure must happen before parsing.
            manifest["sources"] = manifest["sources"][:1]
            source = manifest["sources"][0]
            (root / source["path"]).write_bytes((ROOT / source["path"]).read_bytes() + b"tampered")
            (root / "data/sources/manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "SOURCE_HASH_MISMATCH"):
                build_catalog(root)


if __name__ == "__main__":
    unittest.main()
