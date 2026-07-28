def split_equivocation_targets(targets, sender):
    """Select a deterministic non-empty recipient subset independent of node IDs."""
    recipients = tuple(sorted(set(int(node) for node in targets) - {int(sender)}))
    if not recipients:
        return ()
    selected = recipients[1::2]
    return selected or recipients[-1:]


class FaultPolicy:
    def __init__(
        self, *, scenario, node_id, fault_nodes, ranked_primaries
    ):
        self.scenario = str(scenario).lower()
        self.node_id = int(node_id)
        self.fault_nodes = frozenset(int(node) for node in fault_nodes)
        self.ranked_primaries = tuple(int(node) for node in ranked_primaries)

    def is_primary(self, view):
        return self.node_id == self.ranked_primaries[
            int(view) % len(self.ranked_primaries)
        ]

    def is_target(self, view):
        return self.node_id in self.fault_nodes and self.is_primary(view)

    def before_proposal(self, view):
        view = int(view)
        if not self.is_target(view):
            return None
        if self.scenario == "f1" and view == 0:
            return "crash"
        if self.scenario == "f5" and view in (0, 1):
            return "omit"
        return None

    def after_preprepare(self, view):
        if self.scenario == "f2" and self.is_target(view):
            return "crash"
        return None

    def preprepare_targets(self, view, targets, *, mode, quorum_size):
        targets = tuple(sorted(set(int(node) for node in targets) - {self.node_id}))
        if self.scenario != "f2" or not self.is_target(view):
            return targets
        count = max(1, int(quorum_size) - 2) if mode == "pbft" else 1
        return targets[:count]

    def prepare_targets(self, view, targets, *, mode):
        primary = self.ranked_primaries[int(view) % len(self.ranked_primaries)]
        if (
            self.scenario == "f3"
            and mode == "pbft"
            and primary in self.fault_nodes
        ):
            return (primary,)
        return tuple(sorted(set(int(node) for node in targets) - {self.node_id}))

    def after_group_certificate(self, view, certificate_count):
        if (
            self.scenario == "f2l"
            and self.is_target(view)
            and int(certificate_count) == 1
        ):
            return "crash"
        return None

    def after_protocol_lock(self, view):
        if self.scenario == "f3" and self.is_target(view):
            return "crash"
        return None

    def equivocate_preprepare(self, view):
        return self.scenario == "f4" and self.is_target(view)
