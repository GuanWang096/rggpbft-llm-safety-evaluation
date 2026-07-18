import unittest

from protocol import EquivocationTracker, make_message, quorum, validate_certificate, verify_message


class ProtocolTests(unittest.TestCase):
    def test_signed_tuple_rejects_tampering(self):
        message = make_message("PREPARE", 1, 2, 7, "digest-a", 0)
        self.assertTrue(verify_message(message, range(4)))
        message["digest"] = "digest-b"
        self.assertFalse(verify_message(message, range(4)))

    def test_certificate_requires_distinct_valid_members(self):
        messages = [make_message("COMMIT", sender, 0, 3, "digest", 0) for sender in (0, 1, 2)]
        certificate = validate_certificate(
            messages,
            message_type="COMMIT",
            view=0,
            sequence=3,
            digest="digest",
            group=0,
            allowed_members={0, 1, 2, 3},
            threshold=quorum(4),
        )
        self.assertEqual(len(certificate), 3)
        with self.assertRaises(ValueError):
            validate_certificate(
                [messages[0], messages[0], messages[1]],
                message_type="COMMIT",
                view=0,
                sequence=3,
                digest="digest",
                group=0,
                allowed_members={0, 1, 2, 3},
                threshold=quorum(4),
            )

    def test_equivocation_tracker_reports_conflicting_digest(self):
        tracker = EquivocationTracker()
        first = make_message("PREPARE", 2, 0, 9, "a", 1)
        conflict = make_message("PREPARE", 2, 0, 9, "b", 1)
        self.assertFalse(tracker.observe(first))
        self.assertTrue(tracker.observe(conflict))
        self.assertEqual(len(tracker.conflicts), 1)


if __name__ == "__main__":
    unittest.main()
