"""Bridge path-bounds regression from supervised Qwen's corrected red tests."""
import unittest

from audit_retreat_atomicity import audit


class RetreatAtomicityTest(unittest.TestCase):
    def test_gap_between_chunks_rejects_single_call(self):
        chunks = [
            [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (20.0, 0.0, 0.0)],
            [(25.0, 0.0, 0.0), (35.0, 0.0, 0.0), (45.0, 0.0, 0.0)],
        ]
        self.assertFalse(audit(chunks)['one_call_full_route_possible'])

    def test_single_step_span_over_bridge_rejects_single_call(self):
        chunks = [[(0.0, 0.0, 0.0), (20.0, 0.0, 0.0)]]
        self.assertFalse(audit(chunks)['one_call_full_route_possible'])

    def test_connected_valid_route_allows_single_call(self):
        chunks = [[(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (20.0, 0.0, 0.0)],
                  [(20.0, 0.0, 0.0), (30.0, 0.0, 0.0), (40.0, 0.0, 0.0)]]
        self.assertTrue(audit(chunks)['one_call_full_route_possible'])


if __name__ == '__main__':
    unittest.main()
