"""Minimize billed worker time for a selected collection of tests."""

def schedule(durations):
    """Map numeric test IDs and durations in seconds to at most eight workers."""
    if not durations:
        return []
    jobs = sorted(durations, key=lambda job: (-durations[job], job))
    total = sum(durations.values())
    longest = durations[jobs[0]]
    best_cost = float("inf")
    best = []
    for count in range(1, min(8, len(jobs)) + 1):
        lower_bound = max(longest, total / count) + 2 * count
        if lower_bound >= best_cost:
            continue
        loads = [0.0] * count
        groups = [[] for _ in loads]
        for index, job in enumerate(jobs):
            worker = index if index < count else min(range(count), key=loads.__getitem__)
            groups[worker].append(job)
            loads[worker] += durations[job]
        cost = max(loads) + 2 * count
        if cost < best_cost:
            best = groups
            best_cost = cost
    return best
