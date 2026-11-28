"""Test wave planning and ordering independently of SSH or Ansible."""

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from rollout import activate_stages, schedule


def baseline():
    return {
        "deployment": {
            "mode": "selective",
            "selective": {"components": ["payment", "orders"]},
            "full": {"components": "all"},
            "stages": [
                {"name": "core", "components": ["payment", "orders"], "max_parallel": 2},
                {"name": "last", "components": ["reporting"], "max_parallel": 1},
            ],
            "host_waves": [["a"], ["b", "c"]],
        },
        "components": {
            "payment": {"group": "payment_nodes"},
            "orders": {"group": "orders_nodes", "host_waves": [["c"], ["a"], ["b"]]},
            "reporting": {"group": "reporting_nodes"},
        },
    }


INVENTORY = {
    "payment_nodes": {"hosts": ["a", "b", "c"]},
    "orders_nodes": {"hosts": ["a", "b", "c"]},
    "reporting_nodes": {"hosts": ["b", "c"]},
}


class RolloutTests(unittest.TestCase):
    def test_modes_and_inheritance(self):
        stages, target_info = schedule(baseline(), INVENTORY, "full")
        self.assertEqual([stage["name"] for stage in stages], ["core", "last"])
        self.assertEqual(target_info["payment"][1], [["a"], ["b", "c"]])
        self.assertEqual(target_info["orders"][1], [["c"], ["a"], ["b"]])
        self.assertEqual(target_info["reporting"][1], [[], ["b", "c"]])
        selected, _ = schedule(baseline(), INVENTORY, "selective")
        self.assertEqual([stage["name"] for stage in selected], ["core"])

    def test_explicit_selection_preserves_descriptor_stage_order(self):
        stages, _ = schedule(baseline(), INVENTORY, "selective", ["reporting", "orders"])
        self.assertEqual([stage["components"] for stage in stages], [["orders"], ["reporting"]])

    def test_invalid_explicit_selection_is_rejected(self):
        for mode, selection in (
            ("full", ["payment"]), ("selective", ["unknown"]),
            ("selective", ["payment", "payment"]), ("selective", [""]),
        ):
            with self.subTest(mode=mode, selection=selection):
                with self.assertRaisesRegex(ValueError, "--components"):
                    schedule(baseline(), INVENTORY, mode, selection)

    def test_parallel_components_and_wave_barriers(self):
        stages, targets = schedule(baseline(), INVENTORY, "full")
        plan = {name: {"host_waves": target[1]} for name, target in targets.items()}
        concurrent = threading.Barrier(2)
        ran = set()
        lock = threading.Lock()

        def activate(name, wave):
            if (name, tuple(wave)) in {("payment", ("a",)), ("orders", ("c",))}:
                concurrent.wait(timeout=5)
            with lock:
                if (name, tuple(wave)) in {("payment", ("b", "c")), ("orders", ("a",))}:
                    self.assertTrue({("payment", ("a",)), ("orders", ("c",))} <= ran)
                if name == "reporting":
                    self.assertIn(("orders", ("b",)), ran)
                ran.add((name, tuple(wave)))

        activate_stages(stages, plan, activate, lambda *_: None)
        self.assertEqual(len(ran), 6)

    def test_failed_wave_skips_later_work(self):
        stages, targets = schedule(baseline(), INVENTORY, "full")
        plan = {name: {"host_waves": target[1]} for name, target in targets.items()}
        visited = []

        def activate(name, wave):
            visited.append((name, wave))
            if name == "payment":
                raise RuntimeError("activation failed")

        with self.assertRaisesRegex(RuntimeError, "core, host wave 1"):
            activate_stages(stages, plan, activate, lambda *_: None)
        self.assertCountEqual(visited, [("payment", ["a"]), ("orders", ["c"])])

    def test_max_parallel_one_respects_component_order(self):
        data = baseline()
        data["deployment"]["stages"][0]["max_parallel"] = 1
        stages, targets = schedule(data, INVENTORY, "selective")
        plan = {name: {"host_waves": target[1]} for name, target in targets.items()}
        visited = []
        activate_stages(stages, plan, lambda name, wave: visited.append((name, wave)), lambda *_: None)
        self.assertEqual(visited, [
            ("payment", ["a"]), ("orders", ["c"]),
            ("payment", ["b", "c"]), ("orders", ["a"]), ("orders", ["b"]),
        ])

    def test_unknown_omitted_and_repeated_hosts_fail(self):
        cases = ([["a"], ["b"]], [["a"], ["b", "c", "d"]], [["a"], ["a", "b", "c"]])
        for waves in cases:
            with self.subTest(waves=waves):
                data = baseline()
                data["deployment"]["host_waves"] = waves
                with self.assertRaisesRegex(ValueError, "each target alias exactly once"):
                    schedule(data, INVENTORY, "full")

    def test_schedule_must_cover_all_components(self):
        data = baseline()
        data["deployment"]["stages"].pop()
        with self.assertRaisesRegex(ValueError, "each descriptor component exactly once"):
            schedule(data, INVENTORY, "selective")


if __name__ == "__main__":
    unittest.main()
