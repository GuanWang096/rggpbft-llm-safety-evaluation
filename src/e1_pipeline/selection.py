from collections import defaultdict, deque
import random


def stratified_select(samples, limit: int | None, seed: int):
    samples = list(samples)
    if limit is None or limit >= len(samples):
        return samples
    if limit < 0:
        raise ValueError("limit must be non-negative")

    rng = random.Random(seed)
    grouped = defaultdict(list)
    for sample in samples:
        key = (sample.dataset, sample.variant, sample.risk_category)
        grouped[key].append(sample)
    for group in grouped.values():
        rng.shuffle(group)

    keys_by_stratum = defaultdict(list)
    for key in sorted(grouped):
        keys_by_stratum[(key[0], key[1])].append(key)

    selected = []
    pending = {stratum: deque(keys) for stratum, keys in keys_by_stratum.items()}
    active_strata = deque(sorted(pending))
    while active_strata and len(selected) < limit:
        stratum = active_strata.popleft()
        group_keys = pending[stratum]
        key = group_keys.popleft()
        group = grouped[key]
        selected.append(group.pop())
        if group:
            group_keys.append(key)
        if group_keys:
            active_strata.append(stratum)
    return selected
