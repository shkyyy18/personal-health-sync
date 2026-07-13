import tempfile
import unittest
from pathlib import Path

from healthsync.cli import main
from healthsync.core import daily_summary, generate_demo, import_csv, latest, load, merge, observation, render_dashboard


class CoreTests(unittest.TestCase):
    def test_observation_rejects_non_numeric_values(self):
        with self.assertRaises(ValueError):
            observation("2026-07-13", "weight", "bad")

    def test_observation_rejects_invalid_dates_and_empty_codes(self):
        for value in ("2026-02-30", "not-a-date", "2026-7-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                observation(value, "weight", 72)
        with self.assertRaises(ValueError):
            observation("2026-07-13", "   ", 72)

    def test_latest_and_summary(self):
        items = [observation("2026-07-12", "steps", 100), observation("2026-07-13", "steps", 200)]
        self.assertEqual(latest(items, "steps")["value"], 200)
        self.assertEqual(daily_summary(items, "2026-07-12")["steps"]["value"], 100)

    def test_csv_import_and_idempotent_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "sample.csv"
            csv_path.write_text("date,code,value,unit,source,display\n2026-07-13,body-weight,72.1,kg,scale,Weight\n", encoding="utf-8")
            incoming = import_csv(csv_path)
            store = root / "observations.json"
            merge(store, incoming); merge(store, incoming)
            self.assertEqual(len(load(store)), 1)

    def test_demo_and_dashboard_are_portable(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dashboard.html"
            items = generate_demo(7); render_dashboard(items, output)
            text = output.read_text(encoding="utf-8")
            self.assertIn("Synthetic demo data only", text)
            self.assertIn("body-weight", text)

    def test_dashboard_embedded_json_cannot_close_script_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dashboard.html"
            item = observation("2026-07-13", "note", 1, source="</script><script>alert(1)</script>")
            render_dashboard([item], output)
            text = output.read_text(encoding="utf-8")
            self.assertNotIn("</script><script>alert(1)</script>", text)
            self.assertIn("\\u003c/script\\u003e", text)

    def test_cli_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc = main(["--store", str(root / "store.json"), "demo", "--days", "3", "--dashboard", str(root / "dashboard.html")])
            self.assertEqual(rc, 0)
            self.assertTrue((root / "store.json").exists()); self.assertTrue((root / "dashboard.html").exists())


if __name__ == "__main__":
    unittest.main()
