import tempfile
import unittest
from pathlib import Path
from agent_wow_run_artifacts import validate_run_artifacts


class RunArtifactTests(unittest.TestCase):
    def test_fresh_outputs_and_stale_embedded_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'results/pure_network').mkdir(parents=True)
            self.assertEqual(len(validate_run_artifacts(root,'new',"p='trial_new.json'")['outputs']),9)
            with self.assertRaises(ValueError):
                validate_run_artifacts(root,'new',"p='trial_old.json'")

    def test_packet_collision_refused_even_if_trial_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            target=root/'results/pure_network'
            target.mkdir(parents=True)
            (target/'agent_wow_bridge_new.packets.json').touch()
            with self.assertRaises(FileExistsError):
                validate_run_artifacts(root,'new',"p='trial_new.json'")

    def test_external_output_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'results/pure_network').mkdir(parents=True)
            with self.assertRaises(ValueError):
                validate_run_artifacts(root, 'new', 'pass', extra_outputs=['../outside.json'])


if __name__=='__main__':
    unittest.main()
