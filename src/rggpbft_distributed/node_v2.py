import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fault_policy import FaultPolicy, split_equivocation_targets
from grouping import build_group_map
from network_utils import resolve_host
from protocol import EquivocationTracker, make_message, quorum, validate_certificate, verify_message
from view_change import (
    StaleNewViewError,
    build_pbft_new_view,
    build_rgg_new_view,
    validate_pbft_new_view,
    validate_pbft_view_change,
    validate_global_prepared,
    validate_rgg_new_view,
    validate_rgg_leader_view_change,
)
from view_runtime import (
    NetworkAccounting,
    PendingPrepareBuffer,
    SequenceViewState,
    is_sequence_bootstrap_message,
)


NODE_ID = int(os.environ["NODE_ID"])
M = int(os.environ.get("M", "16"))
K_G = int(os.environ["K_G"]) if os.environ.get("K_G") not in (None, "", "None") else None
PORT = int(os.environ.get("PORT", "9000"))
DELAY_MS = float(os.environ.get("DELAY_MS", "5"))
FAULT_MODE = os.environ.get("FAULT_MODE", "none").lower()
FAULT_SCENARIO = os.environ.get("FAULT_SCENARIO", "none").lower()
FAULT_NODES = {int(value) for value in os.environ.get("FAULT_NODES", "").split(",") if value}
FAULT_DELAY_MS = float(os.environ.get("FAULT_DELAY_MS", "100"))
COLLECTOR_HOST = os.environ.get("COLLECTOR_HOST", "collector")
COLLECTOR_PORT = int(os.environ.get("COLLECTOR_PORT", "9999"))
HARD_TIMEOUT_SECONDS = float(os.environ.get("HARD_TIMEOUT_SECONDS", "600"))
VIEW_TIMEOUT_SECONDS = float(os.environ.get("VIEW_TIMEOUT_SECONDS", "1.0"))
MODE = "RGG-PBFT" if K_G else "PBFT"
MEMBERS = set(range(M))
REPUTATION_ORDER = [int(value) for value in os.environ.get("REPUTATION_ORDER", ",".join(map(str, range(M)))).split(",")]


if K_G is not None:
    _GROUP_MAP, _GROUP_LEADERS, _L_GL = build_group_map(REPUTATION_ORDER, K_G)
else:
    _GROUP_MAP = {}
    _GROUP_LEADERS = {-1: REPUTATION_ORDER[0]}
    _L_GL = tuple(REPUTATION_ORDER)


def group_of(node_id):
    if K_G is None:
        return -1
    return _GROUP_MAP[node_id]


def group_members(group):
    if K_G is None:
        return MEMBERS
    return {node_id for node_id in MEMBERS if _GROUP_MAP.get(node_id) == group}


LEADERS = _GROUP_LEADERS
LEADER_SET = set(LEADERS.values())
GROUP = group_of(NODE_ID)
IS_LEADER = NODE_ID in LEADER_SET
RANKED_PRIMARIES = _L_GL


def frozen_groups():
    if K_G is None:
        return {-1: tuple(sorted(MEMBERS))}
    return {group: tuple(sorted(group_members(group))) for group in range(K_G)}


FROZEN_GROUPS = frozen_groups()
lock = threading.Lock()
states = {}
sequence_states = {}
view_change_messages = {}
new_views_sent = set()
crashed_sequences = set()
fault_injections_reported = set()
accepted_preprepare = {}
pending_prepares = PendingPrepareBuffer()
committed = {}
equivocations = EquivocationTracker()
executor = ThreadPoolExecutor(max_workers=min(64, max(8, M * 2)))
network_accounting = NetworkAccounting()
running = True


FAULT_POLICY = FaultPolicy(
    scenario=FAULT_SCENARIO,
    node_id=NODE_ID,
    fault_nodes=FAULT_NODES,
    ranked_primaries=RANKED_PRIMARIES,
)


def primary_for_view(view):
    return RANKED_PRIMARIES[int(view) % len(RANKED_PRIMARIES)]


def sequence_state(sequence, digest, start_ns):
    runtime = sequence_states.get(sequence)
    if runtime is None:
        runtime = SequenceViewState(
            sequence=sequence,
            initial_digest=digest,
            start_ns=start_ns,
            members=tuple(sorted(MEMBERS)),
            groups=FROZEN_GROUPS,
            leaders=LEADERS,
            rank=RANKED_PRIMARIES,
        )
        runtime.touch(time.monotonic())
        sequence_states[sequence] = runtime
    elif runtime.selected_digest != digest and runtime.committed_digest is None:
        emit(
            "CLIENT_DIGEST_CONFLICT",
            {
                "sequence": sequence,
                "known_digest": runtime.selected_digest,
                "received_digest": digest,
            },
        )
    return runtime


def emit(event_type, data):
    event = {"type": event_type, "node": NODE_ID, "time_ns": time.time_ns(), "data": data}
    try:
        with socket.create_connection((resolve_host(COLLECTOR_HOST), COLLECTOR_PORT), timeout=2) as connection:
            connection.sendall((json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"))
    except OSError:
        pass


def state_for(message):
    runtime = sequence_state(
        message["sequence"],
        message["digest"],
        int(message["payload"].get("start_ns", time.time_ns())),
    )
    key = (message["view"], message["sequence"], message["digest"])
    state = states.setdefault(
        key,
        {
            "view": message["view"],
            "sequence": message["sequence"],
            "digest": message["digest"],
            "start_ns": int(message["payload"].get("start_ns", time.time_ns())),
            "prepares": {},
            "commits": {},
            "group_certificates": {},
            "global_commits": {},
            "sent_prepare": False,
            "sent_commit": False,
            "sent_group_certificate": False,
            "sent_global_commit": False,
            "sent_notify": False,
            "done": False,
            "messages_sent": 0,
            "bytes_sent": 0,
            "preprepare": None,
            "global_prepared": None,
        },
    )
    state["runtime"] = runtime
    return state


def phase_message(message_type, state, group, payload=None):
    body = {"start_ns": state["start_ns"]}
    if payload:
        body.update(payload)
    return make_message(
        message_type,
        NODE_ID,
        state["view"],
        state["sequence"],
        state["digest"],
        group,
        body,
    )


def send_one(target, message, state_key):
    delay = DELAY_MS
    if FAULT_MODE == "delay" and NODE_ID in FAULT_NODES:
        delay += FAULT_DELAY_MS
    time.sleep(delay / 1000)
    try:
        encoded = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with socket.create_connection((resolve_host(f"node{target}"), PORT), timeout=3) as connection:
            connection.sendall(encoded)
        network_accounting.record(message["sequence"], len(encoded))
        with lock:
            if state_key in states:
                states[state_key]["messages_sent"] += 1
                states[state_key]["bytes_sent"] += len(encoded)
    except OSError:
        pass


def dispatch(outgoing):
    for targets, message in outgoing:
        key = (message["view"], message["sequence"], message["digest"])
        recipients = tuple(sorted(set(targets) - {NODE_ID}))
        conflict_targets = set(split_equivocation_targets(recipients, NODE_ID))
        for target in recipients:
            outbound = message
            if (
                (FAULT_MODE == "equivocation" or FAULT_POLICY.equivocate_preprepare(message["view"]))
                and message["sender"] == NODE_ID
                and message["type"] == "PRE_PREPARE"
                and target in conflict_targets
            ):
                outbound = make_message(
                    message["type"],
                    NODE_ID,
                    message["view"],
                    message["sequence"],
                    message["digest"] + "-conflict",
                    message["group"],
                    message["payload"],
                )
                injection_key = (FAULT_SCENARIO, message["sequence"], message["view"])
                if injection_key not in fault_injections_reported:
                    fault_injections_reported.add(injection_key)
                    emit(
                        "FAULT_INJECTED",
                        {
                            "scenario": "f4",
                            "stage": "equivocating_preprepare",
                            "action": "equivocate",
                            "sequence": message["sequence"],
                            "view": message["view"],
                        },
                    )
                emit("EQUIVOCATION_SENT", {"target": target, "original": message, "conflict": outbound})
            executor.submit(send_one, target, outbound, key)


def commit_locked(state, certificate_type):
    prior = committed.get(state["sequence"])
    if prior is not None and prior != state["digest"]:
        return {
            "type": "SAFETY_VIOLATION",
            "data": {"sequence": state["sequence"], "first_digest": prior, "second_digest": state["digest"]},
        }
    if state["done"]:
        return None
    state["done"] = True
    committed[state["sequence"]] = state["digest"]
    state["runtime"].record_commit(state["digest"])
    state["runtime"].touch(time.monotonic())
    network = network_accounting.snapshot(state["sequence"])
    return {
        "type": "COMMIT",
        "data": {
            "mode": MODE,
            "sequence": state["sequence"],
            "view": state["view"],
            "digest": state["digest"],
            "latency_ms": (time.time_ns() - state["start_ns"]) / 1_000_000,
            "certificate_type": certificate_type,
            "messages_sent": network["messages_sent"],
            "bytes_sent": network["bytes_sent"],
        },
    }


def process_prepare_locked(message, state, runtime, outgoing):
    expected_group = -1 if K_G is None else GROUP
    members = group_members(expected_group)
    if message["group"] != expected_group or message["sender"] not in members:
        return
    if accepted_preprepare.get((message["view"], message["sequence"])) != message["digest"]:
        pending_prepares.add(message)
        return
    state["prepares"][message["sender"]] = message
    if len(state["prepares"]) < quorum(len(members)) or state["sent_commit"]:
        return
    if K_G is None and runtime.prepared is None:
        prepared = make_message(
            "PBFT_PREPARED",
            NODE_ID,
            state["view"],
            state["sequence"],
            state["digest"],
            -1,
            {
                "preprepare": state["preprepare"],
                "prepares": list(state["prepares"].values()),
            },
        )
        runtime.record_prepared(state["view"], state["digest"], prepared)
        emit(
            "PBFT_PREPARED",
            {"sequence": state["sequence"], "view": state["view"]},
        )
        action = FAULT_POLICY.after_protocol_lock(state["view"])
        if action == "crash":
            crashed_sequences.add(state["sequence"])
            emit(
                "FAULT_INJECTED",
                {
                    "scenario": FAULT_SCENARIO,
                    "stage": "pbft_prepared",
                    "action": action,
                    "sequence": state["sequence"],
                    "view": state["view"],
                },
            )
            return
    state["sent_commit"] = True
    commit = phase_message("COMMIT", state, expected_group)
    state["commits"][NODE_ID] = commit
    outgoing.append((members, commit))


def accept_preprepare_locked(message, outgoing):
    runtime = sequence_state(
        message["sequence"],
        message["digest"],
        int(message["payload"].get("start_ns", time.time_ns())),
    )
    if message["view"] != runtime.current_view:
        return None
    if message["sender"] != primary_for_view(message["view"]):
        return None
    if message["digest"] != runtime.selected_digest:
        return None
    sequence_key = (message["view"], message["sequence"])
    previous = accepted_preprepare.setdefault(sequence_key, message["digest"])
    if previous != message["digest"]:
        equivocations.observe(message)
        return None
    state = state_for(message)
    state["preprepare"] = message
    runtime.touch(time.monotonic())
    if not state["sent_prepare"]:
        state["sent_prepare"] = True
        vote_group = -1 if K_G is None else GROUP
        prepare = phase_message("PREPARE", state, vote_group)
        state["prepares"][NODE_ID] = prepare
        prepare_targets = FAULT_POLICY.prepare_targets(
            state["view"],
            group_members(vote_group),
            mode="pbft" if K_G is None else "rgg",
        )
        outgoing.append((prepare_targets, prepare))
    for pending in pending_prepares.pop(
        message["view"],
        message["sequence"],
        message["digest"],
        -1 if K_G is None else GROUP,
    ):
        process_prepare_locked(pending, state, runtime, outgoing)
        if message["sequence"] in crashed_sequences:
            break
    return state


def start_preprepare_locked(runtime, outgoing):
    if NODE_ID != primary_for_view(runtime.current_view):
        return
    action = FAULT_POLICY.before_proposal(runtime.current_view)
    if action is not None:
        if action == "crash":
            crashed_sequences.add(runtime.sequence)
        emit(
            "FAULT_INJECTED",
            {
                "scenario": FAULT_SCENARIO,
                "stage": "before_preprepare",
                "action": action,
                "sequence": runtime.sequence,
                "view": runtime.current_view,
            },
        )
        return
    preprepare = make_message(
        "PRE_PREPARE",
        NODE_ID,
        runtime.current_view,
        runtime.sequence,
        runtime.selected_digest,
        -1,
        {"start_ns": runtime.start_ns},
    )
    local_outgoing = []
    state = accept_preprepare_locked(preprepare, local_outgoing)
    if state is None:
        return
    targets = MEMBERS if K_G is None else LEADER_SET | group_members(GROUP)
    target_candidates = (
        targets
        if K_G is None or FAULT_SCENARIO != "f2"
        else group_members(GROUP)
    )
    targets = FAULT_POLICY.preprepare_targets(
        runtime.current_view,
        target_candidates,
        mode="pbft" if K_G is None else "rgg",
        quorum_size=quorum(M if K_G is None else len(group_members(GROUP))),
    )
    outgoing.append((targets, preprepare))
    outgoing.extend(local_outgoing)
    action = FAULT_POLICY.after_preprepare(runtime.current_view)
    if action == "crash":
        crashed_sequences.add(runtime.sequence)
        emit(
            "FAULT_INJECTED",
            {
                "scenario": FAULT_SCENARIO,
                "stage": "after_preprepare",
                "action": action,
                "sequence": runtime.sequence,
                "view": runtime.current_view,
            },
        )
    emit(
        "PRE_PREPARE_SENT",
        {
            "sequence": runtime.sequence,
            "view": runtime.current_view,
            "digest": runtime.selected_digest,
        },
    )


def make_view_change_locked(runtime, target_view):
    if K_G is None:
        prepared = runtime.prepared["certificate"] if runtime.prepared else None
        return make_message(
            "VIEW_CHANGE",
            NODE_ID,
            target_view,
            runtime.sequence,
            prepared["digest"] if prepared else "",
            -1,
            {"prepared": prepared, "start_ns": runtime.start_ns},
        )
    if not IS_LEADER:
        return None
    global_lock = runtime.global_lock["certificate"] if runtime.global_lock else None
    payload = {"global_lock": global_lock, "start_ns": runtime.start_ns}
    if runtime.commit_certificate is not None:
        payload["global_commit_certificate"] = runtime.commit_certificate
    return make_message(
        "LEADER_VIEW_CHANGE",
        NODE_ID,
        target_view,
        runtime.sequence,
        global_lock["digest"] if global_lock else "",
        -1,
        payload,
    )


def process_view_change_locked(message, outgoing):
    if message["sender"] not in (MEMBERS if K_G is None else LEADER_SET):
        return
    sequence = message["sequence"]
    runtime = sequence_states.get(sequence)
    if runtime is None or message["view"] <= runtime.current_view:
        return
    expected_type = "VIEW_CHANGE" if K_G is None else "LEADER_VIEW_CHANGE"
    if message["type"] != expected_type:
        return
    try:
        if K_G is None:
            validate_pbft_view_change(
                message,
                target_view=message["view"],
                sequence=sequence,
                members=tuple(sorted(MEMBERS)),
                ranked_members=RANKED_PRIMARIES,
            )
        else:
            validate_rgg_leader_view_change(
                message,
                target_view=message["view"],
                sequence=sequence,
                groups=FROZEN_GROUPS,
                leaders=LEADERS,
                leader_quorum=quorum(K_G),
            )
    except ValueError as exc:
        emit(
            "INVALID_VIEW_CHANGE",
            {
                "sequence": sequence,
                "view": message["view"],
                "sender": message["sender"],
                "error": str(exc),
            },
        )
        return
    key = (message["view"], sequence)
    bucket = view_change_messages.setdefault(key, {})
    bucket.setdefault(message["sender"], message)
    threshold = quorum(M) if K_G is None else quorum(K_G)
    if NODE_ID != primary_for_view(message["view"]):
        return
    if len(bucket) < threshold or key in new_views_sent:
        return
    action = FAULT_POLICY.before_proposal(message["view"])
    if action is not None:
        if action == "crash":
            crashed_sequences.add(sequence)
        emit(
            "FAULT_INJECTED",
            {
                "scenario": FAULT_SCENARIO,
                "stage": "before_new_view_proposal",
                "action": action,
                "sequence": sequence,
                "view": message["view"],
            },
        )
        return
    changes = [bucket[sender] for sender in sorted(bucket)]
    if K_G is None:
        new_view = build_pbft_new_view(
            sender=NODE_ID,
            target_view=message["view"],
            sequence=sequence,
            view_changes=changes,
            members=tuple(sorted(MEMBERS)),
            ranked_members=RANKED_PRIMARIES,
            fallback_digest=runtime.selected_digest,
            start_ns=runtime.start_ns,
        )
    else:
        new_view = build_rgg_new_view(
            sender=NODE_ID,
            target_view=message["view"],
            sequence=sequence,
            view_changes=changes,
            groups=FROZEN_GROUPS,
            leaders=LEADERS,
            leader_quorum=quorum(K_G),
            ranked_leaders=RANKED_PRIMARIES,
            fallback_digest=runtime.selected_digest,
            start_ns=runtime.start_ns,
        )
    new_views_sent.add(key)
    outgoing.append((MEMBERS, new_view))
    process_new_view_locked(new_view, outgoing)
    emit(
        "NEW_VIEW_SENT",
        {
            "sequence": sequence,
            "view": message["view"],
            "certificate_count": len(changes),
        },
    )


def process_new_view_locked(message, outgoing):
    runtime = sequence_states.get(message["sequence"])
    if runtime is None:
        return
    if K_G is None:
        selected = validate_pbft_new_view(
            message,
            current_view=runtime.current_view,
            sequence=runtime.sequence,
            members=tuple(sorted(MEMBERS)),
            ranked_members=RANKED_PRIMARIES,
        )
    else:
        selected = validate_rgg_new_view(
            message,
            current_view=runtime.current_view,
            sequence=runtime.sequence,
            groups=FROZEN_GROUPS,
            leaders=LEADERS,
            leader_quorum=quorum(K_G),
            ranked_leaders=RANKED_PRIMARIES,
        )
    runtime.advance_view(message["view"], selected)
    runtime.touch(time.monotonic())
    emit(
        "NEW_VIEW_ACCEPTED",
        {
            "sequence": runtime.sequence,
            "view": runtime.current_view,
            "digest": selected,
        },
    )
    preprepare = message["payload"]["preprepare"]
    accepted = accept_preprepare_locked(preprepare, outgoing)
    if accepted is not None and NODE_ID == message["sender"]:
        emit(
            "PRE_PREPARE_SENT",
            {
                "sequence": runtime.sequence,
                "view": runtime.current_view,
                "digest": runtime.selected_digest,
            },
        )


def process_protocol(message):
    outgoing = []
    event = None
    if not verify_message(message, MEMBERS):
        emit("INVALID_SIGNATURE", {"message": message})
        return
    if equivocations.observe(message):
        emit("EQUIVOCATION_OBSERVED", equivocations.conflicts[-1])
    if message.get("sequence") in crashed_sequences:
        return

    with lock:
        message_type = message["type"]
        if message_type in {"VIEW_CHANGE", "LEADER_VIEW_CHANGE"}:
            process_view_change_locked(message, outgoing)
            dispatch(outgoing)
            return
        if message_type == "NEW_VIEW":
            try:
                process_new_view_locked(message, outgoing)
            except StaleNewViewError as exc:
                emit(
                    "STALE_NEW_VIEW_IGNORED",
                    {
                        "sequence": message["sequence"],
                        "view": message["view"],
                        "sender": message["sender"],
                        "error": str(exc),
                    },
                )
            except ValueError as exc:
                emit(
                    "INVALID_NEW_VIEW",
                    {
                        "sequence": message["sequence"],
                        "view": message["view"],
                        "sender": message["sender"],
                        "error": str(exc),
                    },
                )
            dispatch(outgoing)
            return
        if message_type == "PREPARE" and message["sequence"] not in sequence_states:
            pending_prepares.add(message)
            return
        if message["sequence"] not in sequence_states:
            if not is_sequence_bootstrap_message(message, primary_for_view(0)):
                return
            sequence_state(
                message["sequence"],
                message["digest"],
                int(message["payload"].get("start_ns", time.time_ns())),
            )
        state = state_for(message)
        runtime = state["runtime"]
        if message["view"] != runtime.current_view:
            return
        if message["digest"] != runtime.selected_digest:
            return
        runtime.touch(time.monotonic())
        if message_type == "PRE_PREPARE":
            if message["sender"] != primary_for_view(message["view"]) or message["group"] != -1:
                return
            if K_G is not None and IS_LEADER:
                outgoing.append((group_members(GROUP), message))
            accept_preprepare_locked(message, outgoing)

        elif message_type == "PREPARE":
            process_prepare_locked(message, state, runtime, outgoing)

        elif message_type == "COMMIT":
            expected_group = -1 if K_G is None else GROUP
            members = group_members(expected_group)
            if message["group"] != expected_group or message["sender"] not in members:
                return
            state["commits"][message["sender"]] = message
            if state["sent_commit"] and len(state["commits"]) >= quorum(len(members)):
                if K_G is None:
                    event = commit_locked(state, "commit-certificate")
                elif IS_LEADER and not state["sent_group_certificate"]:
                    certificate = validate_certificate(
                        list(state["commits"].values()),
                        message_type="COMMIT",
                        view=state["view"],
                        sequence=state["sequence"],
                        digest=state["digest"],
                        group=GROUP,
                        allowed_members=members,
                        threshold=quorum(len(members)),
                    )
                    state["sent_group_certificate"] = True
                    group_certificate = phase_message(
                        "GROUP_CERTIFICATE", state, -1, {"source_group": GROUP, "certificate": certificate}
                    )
                    state["group_certificates"][GROUP] = group_certificate
                    target_primary = primary_for_view(state["view"])
                    if target_primary == NODE_ID:
                        action = FAULT_POLICY.after_group_certificate(
                            state["view"], len(state["group_certificates"])
                        )
                        if action == "crash":
                            crashed_sequences.add(state["sequence"])
                            emit(
                                "FAULT_INJECTED",
                                {
                                    "scenario": FAULT_SCENARIO,
                                    "stage": "first_group_certificate",
                                    "action": action,
                                    "sequence": state["sequence"],
                                    "view": state["view"],
                                },
                            )
                    else:
                        outgoing.append(({target_primary}, group_certificate))

        elif (
            message_type == "GROUP_CERTIFICATE"
            and K_G is not None
            and NODE_ID == primary_for_view(state["view"])
        ):
            source_group = int(message["payload"].get("source_group", -1))
            if source_group not in LEADERS or message["sender"] != LEADERS[source_group]:
                return
            members = group_members(source_group)
            validate_certificate(
                message["payload"].get("certificate", []),
                message_type="COMMIT",
                view=state["view"],
                sequence=state["sequence"],
                digest=state["digest"],
                group=source_group,
                allowed_members=members,
                threshold=quorum(len(members)),
            )
            state["group_certificates"][source_group] = message
            action = FAULT_POLICY.after_group_certificate(
                state["view"], len(state["group_certificates"])
            )
            if action == "crash":
                crashed_sequences.add(state["sequence"])
                emit(
                    "FAULT_INJECTED",
                    {
                        "scenario": FAULT_SCENARIO,
                        "stage": "first_group_certificate",
                        "action": action,
                        "sequence": state["sequence"],
                        "view": state["view"],
                    },
                )
                return
            if len(state["group_certificates"]) >= quorum(K_G) and not state["sent_global_commit"]:
                global_prepared = make_message(
                    "GLOBAL_PREPARED",
                    NODE_ID,
                    state["view"],
                    state["sequence"],
                    state["digest"],
                    -1,
                    {
                        "group_certificates": list(
                            state["group_certificates"].values()
                        )
                    },
                )
                state["global_prepared"] = global_prepared
                runtime.record_global_lock(
                    state["view"], state["digest"], global_prepared
                )
                state["sent_global_commit"] = True
                global_commit = phase_message("GLOBAL_COMMIT", state, -1)
                state["global_commits"][NODE_ID] = global_commit
                action = FAULT_POLICY.after_protocol_lock(state["view"])
                if action == "crash":
                    outgoing.append((LEADER_SET, global_commit))
                    crashed_sequences.add(state["sequence"])
                    emit(
                        "FAULT_INJECTED",
                        {
                            "scenario": FAULT_SCENARIO,
                            "stage": "global_prepared",
                            "action": action,
                            "sequence": state["sequence"],
                            "view": state["view"],
                        },
                    )
                else:
                    outgoing.append((LEADER_SET, global_prepared))
                    outgoing.append((LEADER_SET, global_commit))

        elif message_type == "GLOBAL_PREPARED" and K_G is not None and IS_LEADER:
            if message["sender"] != primary_for_view(state["view"]):
                return
            validate_global_prepared(message, FROZEN_GROUPS, LEADERS, quorum(K_G))
            state["global_prepared"] = message
            runtime.record_global_lock(state["view"], state["digest"], message)
            if not state["sent_global_commit"]:
                state["sent_global_commit"] = True
                global_commit = phase_message("GLOBAL_COMMIT", state, -1)
                state["global_commits"][NODE_ID] = global_commit
                outgoing.append((LEADER_SET, global_commit))

        elif message_type == "GLOBAL_COMMIT" and K_G is not None and IS_LEADER:
            if message["group"] != -1 or message["sender"] not in LEADER_SET:
                return
            state["global_commits"][message["sender"]] = message
            if (
                runtime.global_lock is not None
                and len(state["global_commits"]) >= quorum(K_G)
                and not state["sent_notify"]
            ):
                certificate = validate_certificate(
                    list(state["global_commits"].values()),
                    message_type="GLOBAL_COMMIT",
                    view=state["view"],
                    sequence=state["sequence"],
                    digest=state["digest"],
                    group=-1,
                    allowed_members=LEADER_SET,
                    threshold=quorum(K_G),
                )
                runtime.record_commit(state["digest"], certificate)
                state["sent_notify"] = True
                notify = phase_message("NOTIFY", state, -1, {"certificate": certificate})
                outgoing.append((MEMBERS, notify))
                event = commit_locked(state, "global-commit-certificate")

        elif message_type == "NOTIFY" and K_G is not None:
            if message["sender"] not in LEADER_SET:
                return
            validate_certificate(
                message["payload"].get("certificate", []),
                message_type="GLOBAL_COMMIT",
                view=state["view"],
                sequence=state["sequence"],
                digest=state["digest"],
                group=-1,
                allowed_members=LEADER_SET,
                threshold=quorum(K_G),
            )
            event = commit_locked(state, "global-commit-certificate")

    dispatch(outgoing)
    if event:
        emit(event["type"], event["data"])


def process_request(message, connection):
    sequence = int(message["sequence"])
    digest = str(message["digest"])
    start_ns = int(message["start_ns"])
    outgoing = []
    with lock:
        runtime = sequence_state(sequence, digest, start_ns)
        if runtime.selected_digest != digest:
            return False
        start_preprepare_locked(runtime, outgoing)
    dispatch(outgoing)
    return False


def view_timeout_loop():
    while running:
        outgoing = []
        events = []
        now = time.monotonic()
        with lock:
            for runtime in sequence_states.values():
                if runtime.committed_digest is not None:
                    continue
                if runtime.sequence in crashed_sequences:
                    continue
                if not runtime.is_timed_out(now, VIEW_TIMEOUT_SECONDS):
                    continue
                if K_G is not None and not IS_LEADER:
                    runtime.touch(now)
                    continue
                target_view = runtime.request_next_view()
                message = make_view_change_locked(runtime, target_view)
                runtime.touch(now)
                if message is None:
                    continue
                target = primary_for_view(target_view)
                if target == NODE_ID:
                    process_view_change_locked(message, outgoing)
                else:
                    outgoing.append(({target}, message))
                events.append(
                    {
                        "sequence": runtime.sequence,
                        "from_view": runtime.current_view,
                        "target_view": target_view,
                        "target_primary": target,
                    }
                )
        dispatch(outgoing)
        for data in events:
            emit("VIEW_CHANGE_SENT", data)
        time.sleep(min(0.05, VIEW_TIMEOUT_SECONDS / 4))


def handle(connection):
    keep_open = False
    try:
        chunks = []
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        message = json.loads(b"".join(chunks).decode("utf-8"))
        if message.get("control") == "PING":
            connection.sendall(b'{"control":"PONG"}\n')
            return
        if message.get("control") == "STOP":
            global running
            running = False
            return
        if message.get("control") == "STATUS":
            sequence = int(message["sequence"])
            with lock:
                digest = committed.get(sequence)
            connection.sendall(
                (
                    json.dumps(
                        {"committed": digest is not None, "digest": digest},
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            )
            return
        if NODE_ID in FAULT_NODES and FAULT_MODE == "crash":
            return
        if message.get("type") == "REQUEST":
            keep_open = process_request(message, connection)
        else:
            if FAULT_MODE == "delay" and NODE_ID in FAULT_NODES:
                time.sleep(FAULT_DELAY_MS / 1000)
            process_protocol(message)
    except Exception as exc:
        emit("NODE_ERROR", {"error": repr(exc)})
    finally:
        if not keep_open:
            connection.close()


def reply_clients():
    while running:
        pending = []
        with lock:
            for state in states.values():
                client = state.get("client")
                if state["done"] and client is not None:
                    state["client"] = None
                    pending.append((client, state["sequence"], state["digest"]))
        for client, sequence, digest in pending:
            try:
                client.sendall((json.dumps({"result": "committed", "sequence": sequence, "digest": digest}) + "\n").encode())
            finally:
                client.close()
        time.sleep(0.01)


def main():
    def hard_stop():
        time.sleep(HARD_TIMEOUT_SECONDS)
        os._exit(2)

    threading.Thread(target=hard_stop, daemon=True).start()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PORT))
    server.listen(256)
    server.settimeout(0.5)
    threading.Thread(target=view_timeout_loop, daemon=True).start()
    emit(
        "READY",
        {
            "mode": MODE,
            "group": GROUP,
            "leader": IS_LEADER,
            "primary": NODE_ID == primary_for_view(0),
            "fault_mode": FAULT_MODE,
        },
    )
    while running:
        try:
            connection, _ = server.accept()
            threading.Thread(target=handle, args=(connection,), daemon=True).start()
        except socket.timeout:
            continue
    server.close()
    executor.shutdown(wait=True, cancel_futures=False)
    network = network_accounting.total()
    emit(
        "STOPPED",
        {
            "committed_sequences": len(committed),
            "equivocations": len(equivocations.conflicts),
            "messages_sent": network["messages_sent"],
            "bytes_sent": network["bytes_sent"],
        },
    )
    os._exit(0)


if __name__ == "__main__":
    main()
