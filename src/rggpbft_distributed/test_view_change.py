import unittest

from protocol import make_message, quorum
from view_change import (
    StaleNewViewError,
    build_pbft_new_view,
    build_rgg_new_view,
    select_pbft_safe_value,
    select_rgg_safe_value,
    validate_pbft_new_view,
    validate_pbft_prepared,
    validate_pbft_view_change,
    validate_global_prepared,
    validate_rgg_leader_view_change,
    validate_rgg_new_view,
)


GROUPS = {
    0: (0, 4, 8, 12),
    1: (1, 5, 9, 13),
    2: (2, 6, 10, 14),
    3: (3, 7, 11, 15),
}
LEADERS = {group: members[0] for group, members in GROUPS.items()}
LEADER_SET = tuple(LEADERS.values())
QG = quorum(len(LEADER_SET))
PBFT_MEMBERS = tuple(range(4))


def group_certificate(group, view, sequence, digest):
    commits = [
        make_message("COMMIT", sender, view, sequence, digest, group)
        for sender in GROUPS[group][: quorum(len(GROUPS[group]))]
    ]
    return make_message(
        "GROUP_CERTIFICATE",
        LEADERS[group],
        view,
        sequence,
        digest,
        -1,
        {"source_group": group, "certificate": commits},
    )


def global_prepared(sender, view, sequence, digest, groups=(0, 1, 2)):
    certificates = [
        group_certificate(group, view, sequence, digest) for group in groups
    ]
    return make_message(
        "GLOBAL_PREPARED",
        sender,
        view,
        sequence,
        digest,
        -1,
        {"group_certificates": certificates},
    )


def global_commits(view, sequence, digest):
    return [
        make_message("GLOBAL_COMMIT", sender, view, sequence, digest, -1)
        for sender in LEADER_SET[:QG]
    ]


def leader_view_change(sender, target_view, sequence, lock=None, committed=False):
    payload = {"global_lock": lock}
    if committed:
        payload["global_commit_certificate"] = global_commits(
            lock["view"], sequence, lock["digest"]
        )
    digest = lock["digest"] if lock else ""
    return make_message(
        "LEADER_VIEW_CHANGE",
        sender,
        target_view,
        sequence,
        digest,
        -1,
        payload,
    )


def pbft_prepared(reporter, view, sequence, digest, prepare_senders=(0, 1, 2)):
    primary = PBFT_MEMBERS[view % len(PBFT_MEMBERS)]
    preprepare = make_message(
        "PRE_PREPARE", primary, view, sequence, digest, -1
    )
    prepares = [
        make_message("PREPARE", sender, view, sequence, digest, -1)
        for sender in prepare_senders
    ]
    return make_message(
        "PBFT_PREPARED",
        reporter,
        view,
        sequence,
        digest,
        -1,
        {"preprepare": preprepare, "prepares": prepares},
    )


def pbft_view_change(sender, target_view, sequence, prepared=None):
    return make_message(
        "VIEW_CHANGE",
        sender,
        target_view,
        sequence,
        prepared["digest"] if prepared else "",
        -1,
        {"prepared": prepared},
    )


class RGGViewChangeTests(unittest.TestCase):
    def test_invalid_leader_view_change_is_rejected_before_caching(self):
        message = leader_view_change(0, 3, 7, None)
        message["sequence"] = 8

        with self.assertRaisesRegex(ValueError, "signature"):
            validate_rgg_leader_view_change(
                message,
                target_view=3,
                sequence=7,
                groups=GROUPS,
                leaders=LEADERS,
                leader_quorum=QG,
            )

    def test_global_prepared_requires_qg_distinct_group_certificates(self):
        prepared = global_prepared(0, 2, 7, "digest-a", groups=(0, 1))

        with self.assertRaisesRegex(ValueError, "group certificates"):
            validate_global_prepared(prepared, GROUPS, LEADERS, QG)

    def test_duplicate_group_certificate_cannot_satisfy_global_quorum(self):
        certificate = group_certificate(0, 2, 7, "digest-a")
        prepared = make_message(
            "GLOBAL_PREPARED",
            0,
            2,
            7,
            "digest-a",
            -1,
            {"group_certificates": [certificate, certificate, certificate]},
        )

        with self.assertRaisesRegex(ValueError, "group certificates"):
            validate_global_prepared(prepared, GROUPS, LEADERS, QG)

    def test_tampered_nested_group_vote_is_rejected(self):
        prepared = global_prepared(0, 2, 7, "digest-a")
        prepared["payload"]["group_certificates"][0]["payload"]["certificate"][0][
            "digest"
        ] = "digest-b"
        prepared = make_message(
            "GLOBAL_PREPARED",
            0,
            2,
            7,
            "digest-a",
            -1,
            prepared["payload"],
        )

        with self.assertRaisesRegex(ValueError, "certificate"):
            validate_global_prepared(prepared, GROUPS, LEADERS, QG)

    def test_highest_global_lock_view_is_selected(self):
        low = global_prepared(0, 2, 7, "digest-a")
        high = global_prepared(1, 3, 7, "digest-b")
        messages = [
            leader_view_change(0, 4, 7, low),
            leader_view_change(1, 4, 7, high),
            leader_view_change(2, 4, 7, None),
        ]

        selected = select_rgg_safe_value(
            messages,
            target_view=4,
            sequence=7,
            groups=GROUPS,
            leaders=LEADERS,
            leader_quorum=QG,
            fallback_digest="fallback",
        )

        self.assertEqual(selected, "digest-b")

    def test_committed_value_is_preserved_over_higher_uncommitted_lock(self):
        committed = global_prepared(0, 2, 7, "digest-a")
        higher = global_prepared(1, 3, 7, "digest-b")
        messages = [
            leader_view_change(0, 4, 7, committed, committed=True),
            leader_view_change(1, 4, 7, higher),
            leader_view_change(2, 4, 7, None),
        ]

        selected = select_rgg_safe_value(
            messages,
            target_view=4,
            sequence=7,
            groups=GROUPS,
            leaders=LEADERS,
            leader_quorum=QG,
            fallback_digest="fallback",
        )

        self.assertEqual(selected, "digest-a")

    def test_conflicting_highest_global_locks_are_rejected(self):
        messages = [
            leader_view_change(0, 4, 7, global_prepared(0, 3, 7, "digest-a")),
            leader_view_change(1, 4, 7, global_prepared(1, 3, 7, "digest-b")),
            leader_view_change(2, 4, 7, None),
        ]

        with self.assertRaisesRegex(ValueError, "conflicting global locks"):
            select_rgg_safe_value(
                messages,
                target_view=4,
                sequence=7,
                groups=GROUPS,
                leaders=LEADERS,
                leader_quorum=QG,
                fallback_digest="fallback",
            )

    def test_new_view_carries_all_received_changes_and_nonleader_recomputes(self):
        lock = global_prepared(0, 2, 7, "digest-a")
        messages = [
            leader_view_change(leader, 3, 7, lock if leader == 0 else None)
            for leader in LEADER_SET
        ]

        new_view = build_rgg_new_view(
            sender=LEADERS[3],
            target_view=3,
            sequence=7,
            view_changes=messages,
            groups=GROUPS,
            leaders=LEADERS,
            leader_quorum=QG,
            ranked_leaders=LEADER_SET,
            fallback_digest="fallback",
        )

        self.assertEqual(len(new_view["payload"]["view_changes"]), 4)
        self.assertEqual(new_view["payload"]["preprepare"]["type"], "PRE_PREPARE")
        self.assertEqual(new_view["payload"]["preprepare"]["digest"], "digest-a")
        selected = validate_rgg_new_view(
            new_view,
            current_view=2,
            sequence=7,
            groups=GROUPS,
            leaders=LEADERS,
            leader_quorum=QG,
            ranked_leaders=LEADER_SET,
        )
        self.assertEqual(selected, "digest-a")

    def test_nonleader_rejects_primary_selection_that_differs_from_certificate(self):
        lock = global_prepared(0, 2, 7, "digest-a")
        messages = [
            leader_view_change(0, 3, 7, lock),
            leader_view_change(1, 3, 7, None),
            leader_view_change(2, 3, 7, None),
        ]
        new_view = make_message(
            "NEW_VIEW",
            LEADERS[3],
            3,
            7,
            "digest-b",
            -1,
            {
                "mode": "rgg",
                "view_changes": messages,
                "fallback_digest": "fallback",
                "preprepare": make_message(
                    "PRE_PREPARE", LEADERS[3], 3, 7, "digest-b", -1
                ),
            },
        )

        with self.assertRaisesRegex(ValueError, "selected digest"):
            validate_rgg_new_view(
                new_view,
                current_view=2,
                sequence=7,
                groups=GROUPS,
                leaders=LEADERS,
                leader_quorum=QG,
                ranked_leaders=LEADER_SET,
            )

    def test_nonleader_rejects_stale_new_view(self):
        messages = [leader_view_change(sender, 3, 7, None) for sender in LEADER_SET[:QG]]
        new_view = build_rgg_new_view(
            sender=LEADERS[3],
            target_view=3,
            sequence=7,
            view_changes=messages,
            groups=GROUPS,
            leaders=LEADERS,
            leader_quorum=QG,
            ranked_leaders=LEADER_SET,
            fallback_digest="fallback",
        )

        with self.assertRaisesRegex(StaleNewViewError, "stale"):
            validate_rgg_new_view(
                new_view,
                current_view=3,
                sequence=7,
                groups=GROUPS,
                leaders=LEADERS,
                leader_quorum=QG,
                ranked_leaders=LEADER_SET,
            )


class PBFTViewChangeTests(unittest.TestCase):
    def test_invalid_pbft_view_change_is_rejected_before_caching(self):
        message = pbft_view_change(0, 1, 5, None)
        message["view"] = 2

        with self.assertRaisesRegex(ValueError, "signature"):
            validate_pbft_view_change(
                message,
                target_view=1,
                sequence=5,
                members=PBFT_MEMBERS,
                ranked_members=PBFT_MEMBERS,
            )

    def test_prepared_certificate_requires_pbft_quorum(self):
        prepared = pbft_prepared(
            reporter=0,
            view=0,
            sequence=5,
            digest="digest-a",
            prepare_senders=(0, 1),
        )

        with self.assertRaisesRegex(ValueError, "PREPARE"):
            validate_pbft_prepared(prepared, PBFT_MEMBERS, PBFT_MEMBERS)

    def test_highest_pbft_prepared_view_is_selected(self):
        low = pbft_prepared(0, 0, 5, "digest-a")
        high = pbft_prepared(1, 1, 5, "digest-b")
        changes = [
            pbft_view_change(0, 2, 5, low),
            pbft_view_change(1, 2, 5, high),
            pbft_view_change(2, 2, 5, None),
        ]

        selected = select_pbft_safe_value(
            changes,
            target_view=2,
            sequence=5,
            members=PBFT_MEMBERS,
            ranked_members=PBFT_MEMBERS,
            fallback_digest="fallback",
        )

        self.assertEqual(selected, "digest-b")

    def test_duplicate_view_change_cannot_satisfy_pbft_quorum(self):
        change = pbft_view_change(0, 1, 5, None)

        with self.assertRaisesRegex(ValueError, "distinct VIEW_CHANGE"):
            select_pbft_safe_value(
                [change, change, change],
                target_view=1,
                sequence=5,
                members=PBFT_MEMBERS,
                ranked_members=PBFT_MEMBERS,
                fallback_digest="fallback",
            )

    def test_conflicting_highest_pbft_prepared_values_are_rejected(self):
        changes = [
            pbft_view_change(0, 2, 5, pbft_prepared(0, 1, 5, "digest-a")),
            pbft_view_change(1, 2, 5, pbft_prepared(1, 1, 5, "digest-b")),
            pbft_view_change(2, 2, 5, None),
        ]

        with self.assertRaisesRegex(ValueError, "conflicting PBFT prepared"):
            select_pbft_safe_value(
                changes,
                target_view=2,
                sequence=5,
                members=PBFT_MEMBERS,
                ranked_members=PBFT_MEMBERS,
                fallback_digest="fallback",
            )

    def test_pbft_new_view_is_independently_recomputed(self):
        prepared = pbft_prepared(0, 0, 5, "digest-a")
        changes = [
            pbft_view_change(0, 1, 5, prepared),
            pbft_view_change(1, 1, 5, None),
            pbft_view_change(2, 1, 5, None),
        ]
        new_view = build_pbft_new_view(
            sender=1,
            target_view=1,
            sequence=5,
            view_changes=changes,
            members=PBFT_MEMBERS,
            ranked_members=PBFT_MEMBERS,
            fallback_digest="fallback",
        )

        selected = validate_pbft_new_view(
            new_view,
            current_view=0,
            sequence=5,
            members=PBFT_MEMBERS,
            ranked_members=PBFT_MEMBERS,
        )

        self.assertEqual(selected, "digest-a")
        self.assertEqual(new_view["payload"]["preprepare"]["digest"], "digest-a")

    def test_pbft_new_view_rejects_tampered_embedded_preprepare(self):
        changes = [pbft_view_change(sender, 1, 5, None) for sender in (0, 1, 2)]
        new_view = build_pbft_new_view(
            sender=1,
            target_view=1,
            sequence=5,
            view_changes=changes,
            members=PBFT_MEMBERS,
            ranked_members=PBFT_MEMBERS,
            fallback_digest="fallback",
        )
        new_view["payload"]["preprepare"]["digest"] = "tampered"
        new_view = make_message(
            "NEW_VIEW", 1, 1, 5, "fallback", -1, new_view["payload"]
        )

        with self.assertRaisesRegex(ValueError, "embedded PRE_PREPARE"):
            validate_pbft_new_view(
                new_view,
                current_view=0,
                sequence=5,
                members=PBFT_MEMBERS,
                ranked_members=PBFT_MEMBERS,
            )


if __name__ == "__main__":
    unittest.main()
