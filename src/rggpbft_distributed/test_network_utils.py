import socket
import unittest
from unittest.mock import patch

import network_utils


class NetworkUtilsTests(unittest.TestCase):
    def setUp(self):
        network_utils.clear_cache()

    @patch.object(network_utils.time, "sleep")
    @patch.object(network_utils.socket, "gethostbyname")
    def test_resolve_host_retries_and_caches(self, gethostbyname, _sleep):
        gethostbyname.side_effect = [socket.gaierror("temporary DNS failure"), "172.18.0.5"]

        self.assertEqual(network_utils.resolve_host("node5"), "172.18.0.5")
        self.assertEqual(network_utils.resolve_host("node5"), "172.18.0.5")
        self.assertEqual(gethostbyname.call_count, 2)

if __name__ == "__main__":
    unittest.main()
