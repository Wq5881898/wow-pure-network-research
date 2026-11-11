import unittest
from unittest.mock import patch
from agent_wow_session import PureNetworkBackend


class StreamClockTests(unittest.TestCase):
    def test_packet_clock_reanchor_cannot_reverse_contact_time(self):
        backend = PureNetworkBackend('offline_clock')
        backend.packets = [{'monotonic_ms':319964}]
        backend._sample_wall = 10
        with patch('agent_wow_session.time.monotonic', return_value=10.014):
            contact = backend.stream_now()
        backend._sample_wall = 10.014
        with patch('agent_wow_session.time.monotonic', return_value=10.015):
            self.assertGreaterEqual(backend.stream_now(), contact)


if __name__ == '__main__':
    unittest.main()
