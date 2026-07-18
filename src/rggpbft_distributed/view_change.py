from protocol import make_message, quorum, validate_certificate, verify_message


class StaleNewViewError(ValueError):
    pass


def _leader_set(leaders):
    return tuple(leaders[group] for group in sorted(leaders))


def validate_group_certificate(message, groups, leaders):
    leader_set = _leader_set(leaders)
    if not verify_message(message, leader_set):
        raise ValueError("invalid group certificate signature")
    if message["type"] != "GROUP_CERTIFICATE" or message["group"] != -1:
        raise ValueError("invalid group certificate envelope")
    source_group = int(message["payload"].get("source_group", -1))
    if source_group not in groups or source_group not in leaders:
        raise ValueError("unknown source group")
    if message["sender"] != leaders[source_group]:
        raise ValueError("group certificate sender is not the frozen group leader")
    members = tuple(groups[source_group])
    validate_certificate(
        message["payload"].get("certificate", []),
        message_type="COMMIT",
        view=message["view"],
        sequence=message["sequence"],
        digest=message["digest"],
        group=source_group,
        allowed_members=members,
        threshold=quorum(len(members)),
    )
    return source_group


def validate_global_prepared(message, groups, leaders, leader_quorum):
    leader_set = _leader_set(leaders)
    if not verify_message(message, leader_set):
        raise ValueError("invalid GLOBAL_PREPARED signature")
    if message["type"] != "GLOBAL_PREPARED" or message["group"] != -1:
        raise ValueError("invalid GLOBAL_PREPARED envelope")
    certificates = message["payload"].get("group_certificates", [])
    accepted = {}
    for certificate in certificates:
        if (
            certificate.get("view") != message["view"]
            or certificate.get("sequence") != message["sequence"]
            or certificate.get("digest") != message["digest"]
        ):
            raise ValueError("group certificate tuple does not match GLOBAL_PREPARED")
        source_group = validate_group_certificate(certificate, groups, leaders)
        accepted.setdefault(source_group, certificate)
    if len(accepted) < leader_quorum:
        raise ValueError(
            f"GLOBAL_PREPARED has {len(accepted)} valid distinct group certificates; "
            f"need {leader_quorum}"
        )
    return [accepted[group] for group in sorted(accepted)]


def _validate_leader_view_change(
    message, *, target_view, sequence, groups, leaders, leader_quorum
):
    leader_set = _leader_set(leaders)
    if not verify_message(message, leader_set):
        raise ValueError("invalid LEADER_VIEW_CHANGE signature")
    expected = ("LEADER_VIEW_CHANGE", target_view, sequence, -1)
    actual = (
        message["type"],
        message["view"],
        message["sequence"],
        message["group"],
    )
    if actual != expected:
        raise ValueError("LEADER_VIEW_CHANGE tuple mismatch")
    lock = message["payload"].get("global_lock")
    commit_certificate = message["payload"].get("global_commit_certificate")
    if lock is None:
        if message["digest"] or commit_certificate:
            raise ValueError("lock-free LEADER_VIEW_CHANGE contains lock data")
        return {"message": message, "lock": None, "committed": False}
    if int(lock["view"]) >= target_view:
        raise ValueError("global lock view must precede target view")
    if lock["sequence"] != sequence or lock["digest"] != message["digest"]:
        raise ValueError("global lock tuple mismatch")
    validate_global_prepared(lock, groups, leaders, leader_quorum)
    committed = commit_certificate is not None
    if committed:
        validate_certificate(
            commit_certificate,
            message_type="GLOBAL_COMMIT",
            view=lock["view"],
            sequence=sequence,
            digest=lock["digest"],
            group=-1,
            allowed_members=leader_set,
            threshold=leader_quorum,
        )
    return {"message": message, "lock": lock, "committed": committed}


def validate_rgg_leader_view_change(
    message, *, target_view, sequence, groups, leaders, leader_quorum
):
    return _validate_leader_view_change(
        message,
        target_view=target_view,
        sequence=sequence,
        groups=groups,
        leaders=leaders,
        leader_quorum=leader_quorum,
    )


def _validated_view_changes(
    view_changes, *, target_view, sequence, groups, leaders, leader_quorum
):
    accepted = {}
    for message in view_changes:
        validated = _validate_leader_view_change(
            message,
            target_view=target_view,
            sequence=sequence,
            groups=groups,
            leaders=leaders,
            leader_quorum=leader_quorum,
        )
        accepted.setdefault(message["sender"], validated)
    if len(accepted) < leader_quorum:
        raise ValueError(
            f"NEW_VIEW has {len(accepted)} valid distinct leader changes; "
            f"need {leader_quorum}"
        )
    return [accepted[sender] for sender in sorted(accepted)]


def select_rgg_safe_value(
    view_changes,
    *,
    target_view,
    sequence,
    groups,
    leaders,
    leader_quorum,
    fallback_digest,
):
    validated = _validated_view_changes(
        view_changes,
        target_view=target_view,
        sequence=sequence,
        groups=groups,
        leaders=leaders,
        leader_quorum=leader_quorum,
    )
    committed = [item["lock"] for item in validated if item["committed"]]
    if committed:
        digests = {lock["digest"] for lock in committed}
        if len(digests) != 1:
            raise ValueError("conflicting committed digests in leader view changes")
        return next(iter(digests))
    locks = [item["lock"] for item in validated if item["lock"] is not None]
    if not locks:
        if not fallback_digest:
            raise ValueError("NEW_VIEW has no lock and no fallback digest")
        return fallback_digest
    highest_view = max(lock["view"] for lock in locks)
    highest_digests = {
        lock["digest"] for lock in locks if lock["view"] == highest_view
    }
    if len(highest_digests) != 1:
        raise ValueError("conflicting global locks at the highest lock view")
    return next(iter(highest_digests))


def expected_primary(ranked_leaders, view):
    ranked_leaders = tuple(ranked_leaders)
    if not ranked_leaders:
        raise ValueError("ranked leader sequence is empty")
    return ranked_leaders[view % len(ranked_leaders)]


def _validate_embedded_preprepare(message, allowed_members):
    preprepare = message["payload"].get("preprepare")
    if not preprepare or not verify_message(preprepare, allowed_members):
        raise ValueError("invalid embedded PRE_PREPARE signature")
    expected = (
        "PRE_PREPARE",
        message["sender"],
        message["view"],
        message["sequence"],
        message["digest"],
        -1,
    )
    actual = (
        preprepare.get("type"),
        preprepare.get("sender"),
        preprepare.get("view"),
        preprepare.get("sequence"),
        preprepare.get("digest"),
        preprepare.get("group"),
    )
    if actual != expected:
        raise ValueError("embedded PRE_PREPARE tuple mismatch")
    return preprepare


def validate_pbft_prepared(message, members, ranked_members):
    members = tuple(members)
    if not verify_message(message, members):
        raise ValueError("invalid PBFT_PREPARED signature")
    if message["type"] != "PBFT_PREPARED" or message["group"] != -1:
        raise ValueError("invalid PBFT_PREPARED envelope")
    preprepare = message["payload"].get("preprepare")
    if not preprepare or not verify_message(preprepare, members):
        raise ValueError("invalid prepared PRE_PREPARE")
    expected_sender = expected_primary(ranked_members, message["view"])
    expected_tuple = (
        "PRE_PREPARE",
        expected_sender,
        message["view"],
        message["sequence"],
        message["digest"],
        -1,
    )
    actual_tuple = (
        preprepare["type"],
        preprepare["sender"],
        preprepare["view"],
        preprepare["sequence"],
        preprepare["digest"],
        preprepare["group"],
    )
    if actual_tuple != expected_tuple:
        raise ValueError("prepared PRE_PREPARE tuple mismatch")
    try:
        validate_certificate(
            message["payload"].get("prepares", []),
            message_type="PREPARE",
            view=message["view"],
            sequence=message["sequence"],
            digest=message["digest"],
            group=-1,
            allowed_members=members,
            threshold=quorum(len(members)),
        )
    except ValueError as exc:
        raise ValueError(f"invalid prepared PREPARE certificate: {exc}") from exc
    return message


def _validated_pbft_view_changes(
    view_changes, *, target_view, sequence, members, ranked_members
):
    members = tuple(members)
    accepted = {}
    for message in view_changes:
        prepared = validate_pbft_view_change(
            message,
            target_view=target_view,
            sequence=sequence,
            members=members,
            ranked_members=ranked_members,
        )["prepared"]
        accepted.setdefault(
            message["sender"], {"message": message, "prepared": prepared}
        )
    threshold = quorum(len(members))
    if len(accepted) < threshold:
        raise ValueError(
            f"NEW_VIEW has {len(accepted)} valid distinct VIEW_CHANGE messages; "
            f"need {threshold}"
        )
    return [accepted[sender] for sender in sorted(accepted)]


def validate_pbft_view_change(
    message, *, target_view, sequence, members, ranked_members
):
    members = tuple(members)
    if not verify_message(message, members):
        raise ValueError("invalid VIEW_CHANGE signature")
    expected = ("VIEW_CHANGE", target_view, sequence, -1)
    actual = (
        message["type"],
        message["view"],
        message["sequence"],
        message["group"],
    )
    if actual != expected:
        raise ValueError("VIEW_CHANGE tuple mismatch")
    prepared = message["payload"].get("prepared")
    if prepared is None:
        if message["digest"]:
            raise ValueError("lock-free VIEW_CHANGE contains a digest")
    else:
        if prepared["view"] >= target_view:
            raise ValueError("prepared view must precede target view")
        if prepared["sequence"] != sequence or prepared["digest"] != message["digest"]:
            raise ValueError("prepared tuple does not match VIEW_CHANGE")
        validate_pbft_prepared(prepared, members, ranked_members)
    return {"message": message, "prepared": prepared}


def select_pbft_safe_value(
    view_changes,
    *,
    target_view,
    sequence,
    members,
    ranked_members,
    fallback_digest,
):
    validated = _validated_pbft_view_changes(
        view_changes,
        target_view=target_view,
        sequence=sequence,
        members=members,
        ranked_members=ranked_members,
    )
    prepared = [item["prepared"] for item in validated if item["prepared"] is not None]
    if not prepared:
        if not fallback_digest:
            raise ValueError("PBFT NEW_VIEW has no prepared value and no fallback digest")
        return fallback_digest
    highest_view = max(item["view"] for item in prepared)
    digests = {item["digest"] for item in prepared if item["view"] == highest_view}
    if len(digests) != 1:
        raise ValueError("conflicting PBFT prepared values at the highest view")
    return next(iter(digests))


def build_pbft_new_view(
    *,
    sender,
    target_view,
    sequence,
    view_changes,
    members,
    ranked_members,
    fallback_digest,
    start_ns=None,
):
    if sender != expected_primary(ranked_members, target_view):
        raise ValueError("PBFT NEW_VIEW sender is not the expected primary")
    changes = list(view_changes)
    selected = select_pbft_safe_value(
        changes,
        target_view=target_view,
        sequence=sequence,
        members=members,
        ranked_members=ranked_members,
        fallback_digest=fallback_digest,
    )
    preprepare_payload = {} if start_ns is None else {"start_ns": int(start_ns)}
    preprepare = make_message(
        "PRE_PREPARE",
        sender,
        target_view,
        sequence,
        selected,
        -1,
        preprepare_payload,
    )
    return make_message(
        "NEW_VIEW",
        sender,
        target_view,
        sequence,
        selected,
        -1,
        {
            "mode": "pbft",
            "view_changes": changes,
            "fallback_digest": fallback_digest,
            "preprepare": preprepare,
        },
    )


def validate_pbft_new_view(
    message, *, current_view, sequence, members, ranked_members
):
    members = tuple(members)
    if not verify_message(message, members):
        raise ValueError("invalid PBFT NEW_VIEW signature")
    if (
        message["type"] != "NEW_VIEW"
        or message["group"] != -1
        or message["payload"].get("mode") != "pbft"
    ):
        raise ValueError("invalid PBFT NEW_VIEW envelope")
    if message["sequence"] != sequence:
        raise ValueError("PBFT NEW_VIEW targets the wrong sequence")
    if message["view"] <= current_view:
        raise StaleNewViewError("PBFT NEW_VIEW is stale")
    if message["sender"] != expected_primary(ranked_members, message["view"]):
        raise ValueError("PBFT NEW_VIEW sender is not the expected primary")
    _validate_embedded_preprepare(message, members)
    selected = select_pbft_safe_value(
        message["payload"].get("view_changes", []),
        target_view=message["view"],
        sequence=sequence,
        members=members,
        ranked_members=ranked_members,
        fallback_digest=message["payload"].get("fallback_digest", ""),
    )
    if selected != message["digest"]:
        raise ValueError("PBFT NEW_VIEW selected digest does not match its certificates")
    return selected


def build_rgg_new_view(
    *,
    sender,
    target_view,
    sequence,
    view_changes,
    groups,
    leaders,
    leader_quorum,
    ranked_leaders,
    fallback_digest,
    start_ns=None,
):
    if sender != expected_primary(ranked_leaders, target_view):
        raise ValueError("NEW_VIEW sender is not the expected primary")
    changes = list(view_changes)
    selected = select_rgg_safe_value(
        changes,
        target_view=target_view,
        sequence=sequence,
        groups=groups,
        leaders=leaders,
        leader_quorum=leader_quorum,
        fallback_digest=fallback_digest,
    )
    preprepare_payload = {} if start_ns is None else {"start_ns": int(start_ns)}
    preprepare = make_message(
        "PRE_PREPARE",
        sender,
        target_view,
        sequence,
        selected,
        -1,
        preprepare_payload,
    )
    return make_message(
        "NEW_VIEW",
        sender,
        target_view,
        sequence,
        selected,
        -1,
        {
            "mode": "rgg",
            "view_changes": changes,
            "fallback_digest": fallback_digest,
            "preprepare": preprepare,
        },
    )


def validate_rgg_new_view(
    message,
    *,
    current_view,
    sequence,
    groups,
    leaders,
    leader_quorum,
    ranked_leaders,
):
    leader_set = _leader_set(leaders)
    if not verify_message(message, leader_set):
        raise ValueError("invalid NEW_VIEW signature")
    if (
        message["type"] != "NEW_VIEW"
        or message["group"] != -1
        or message["payload"].get("mode") != "rgg"
    ):
        raise ValueError("invalid NEW_VIEW envelope")
    if message["sequence"] != sequence:
        raise ValueError("NEW_VIEW targets the wrong sequence")
    if message["view"] <= current_view:
        raise StaleNewViewError("NEW_VIEW is stale")
    if message["sender"] != expected_primary(ranked_leaders, message["view"]):
        raise ValueError("NEW_VIEW sender is not the expected primary")
    _validate_embedded_preprepare(message, leader_set)
    selected = select_rgg_safe_value(
        message["payload"].get("view_changes", []),
        target_view=message["view"],
        sequence=sequence,
        groups=groups,
        leaders=leaders,
        leader_quorum=leader_quorum,
        fallback_digest=message["payload"].get("fallback_digest", ""),
    )
    if selected != message["digest"]:
        raise ValueError("NEW_VIEW selected digest does not match its certificates")
    return selected
