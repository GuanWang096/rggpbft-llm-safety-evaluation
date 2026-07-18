import socket
import unittest
from unittest.mock import MagicMock, patch

import driver_v2


class DriverTests(unittest.TestCase):
    def test_normal_runs_wait_for_all_nodes_while_fault_runs_wait_for_one(self):
        self.assertEqual(driver_v2.required_commit_count("none", 24), 24)
        self.assertEqual(driver_v2.required_commit_count("f1", 24), 1)

    @patch.object(driver_v2.time, "sleep")
    @patch.object(driver_v2, "resolve_host", return_value="127.0.0.1")
    @patch.object(driver_v2.socket, "create_connection")
    def test_emit_retries_transient_connection_failure(self, create_connection, _resolve, _sleep):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        create_connection.side_effect = [socket.gaierror("temporary DNS failure"), connection]

        driver_v2.emit("TEST", {"value": 1})

        self.assertEqual(create_connection.call_count, 2)
        connection.sendall.assert_called_once()

    @patch.object(driver_v2, "M", 1)
    @patch.object(driver_v2.time, "sleep")
    @patch.object(driver_v2, "resolve_host", return_value="127.0.0.1")
    @patch.object(driver_v2.socket, "create_connection")
    def test_stop_nodes_retries_connection_failure(self, create_connection, _resolve, _sleep):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        create_connection.side_effect = [OSError("busy"), connection]

        driver_v2.stop_nodes()

        self.assertEqual(create_connection.call_count, 2)
        connection.sendall.assert_called_once()

    @patch.object(driver_v2, "resolve_host", return_value="127.0.0.1")
    @patch.object(driver_v2.socket, "create_connection")
    def test_announce_request_sends_the_same_request_to_every_node(
        self, create_connection, _resolve
    ):
        connections = []
        for _ in range(4):
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connections.append(connection)
        create_connection.side_effect = connections
        request = {
            "type": "REQUEST",
            "sequence": 3,
            "digest": "digest-a",
            "start_ns": 123,
        }

        with patch.object(driver_v2, "M", 4):
            driver_v2.announce_request(request)

        self.assertEqual(create_connection.call_count, 4)
        payloads = [connection.sendall.call_args.args[0] for connection in connections]
        self.assertEqual(len(set(payloads)), 1)

    @patch.object(driver_v2, "resolve_host", return_value="127.0.0.1")
    @patch.object(driver_v2.socket, "create_connection")
    def test_query_commit_accepts_only_the_expected_digest(
        self, create_connection, _resolve
    ):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b'{"committed":true,"digest":"digest-a"}\n'
        create_connection.return_value = connection

        self.assertTrue(driver_v2.query_commit(0, 3, "digest-a"))
        self.assertFalse(driver_v2.query_commit(0, 3, "digest-b"))


if __name__ == "__main__":
    unittest.main()
