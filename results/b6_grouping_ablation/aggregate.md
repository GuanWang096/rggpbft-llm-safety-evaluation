# B6 Grouping Ablation Aggregate

Total runs: 120
## M=16
### fixed_modulo / build-then-exploit (n=10)
- max_group_concentration: 1.00 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
- groups_exceeding_local_threshold: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
- byzantine_leader_count: 2.00 mean, [2, 2, 2, 2, 2, 2, 2, 2, 2, 2]

### fixed_modulo / separable (n=10)
- max_group_concentration: 1.00 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
- groups_exceeding_local_threshold: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
- byzantine_leader_count: 2.00 mean, [2, 2, 2, 2, 2, 2, 2, 2, 2, 2]

### seeded_random / build-then-exploit (n=10)
- max_group_concentration: 1.20 mean, [1, 1, 1, 1, 1, 1, 1, 1, 2, 2]
- groups_exceeding_local_threshold: 0.20 mean, [0, 0, 0, 0, 0, 0, 0, 0, 1, 1]
- byzantine_leader_count: 0.20 mean, [0, 0, 0, 0, 0, 0, 0, 0, 1, 1]

### seeded_random / separable (n=10)
- max_group_concentration: 1.40 mean, [1, 1, 1, 1, 1, 1, 2, 2, 2, 2]
- groups_exceeding_local_threshold: 0.40 mean, [0, 0, 0, 0, 0, 0, 1, 1, 1, 1]
- byzantine_leader_count: 0.20 mean, [0, 0, 0, 0, 0, 0, 0, 0, 1, 1]

### reputation_round_robin / build-then-exploit (n=10)
- max_group_concentration: 1.00 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
- groups_exceeding_local_threshold: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
- byzantine_leader_count: 2.00 mean, [2, 2, 2, 2, 2, 2, 2, 2, 2, 2]

### reputation_round_robin / separable (n=10)
- max_group_concentration: 1.00 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
- groups_exceeding_local_threshold: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
- byzantine_leader_count: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

## M=24
### fixed_modulo / build-then-exploit (n=10)
- max_group_concentration: 1.00 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
- groups_exceeding_local_threshold: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
- byzantine_leader_count: 2.00 mean, [2, 2, 2, 2, 2, 2, 2, 2, 2, 2]

### fixed_modulo / separable (n=10)
- max_group_concentration: 1.00 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
- groups_exceeding_local_threshold: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
- byzantine_leader_count: 2.00 mean, [2, 2, 2, 2, 2, 2, 2, 2, 2, 2]

### seeded_random / build-then-exploit (n=10)
- max_group_concentration: 1.10 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 2]
- groups_exceeding_local_threshold: 0.10 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
- byzantine_leader_count: 0.40 mean, [0, 0, 0, 0, 0, 0, 0, 1, 1, 2]

### seeded_random / separable (n=10)
- max_group_concentration: 1.30 mean, [1, 1, 1, 1, 1, 1, 1, 2, 2, 2]
- groups_exceeding_local_threshold: 0.30 mean, [0, 0, 0, 0, 0, 0, 0, 1, 1, 1]
- byzantine_leader_count: 0.10 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 1]

### reputation_round_robin / build-then-exploit (n=10)
- max_group_concentration: 1.00 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
- groups_exceeding_local_threshold: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
- byzantine_leader_count: 2.00 mean, [2, 2, 2, 2, 2, 2, 2, 2, 2, 2]

### reputation_round_robin / separable (n=10)
- max_group_concentration: 1.00 mean, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
- groups_exceeding_local_threshold: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
- byzantine_leader_count: 0.00 mean, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

