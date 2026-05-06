# TENT Failover — Roadmap & Next Steps

This document tracks the work remaining after the initial failover series was merged
(PRs #1866, #1877, #1878, #1897, #1907, #1984).

## Completed Work

| PR | Date | Description |
|----|------|-------------|
| #1866 | 2026-04-10 | TCP Transport: retry with exponential backoff, async dispatch, graceful shutdown |
| #1877 | 2026-04-13 | Memory registration: respect explicit `MemoryOptions::type`, fix same-machine detection |
| #1878 | 2026-04-13 | **Core**: activate cross-transport failover (RDMA → TCP) with limits and metrics |
| #1897 | 2026-04-15 | Endpoint lifecycle safety: `weak_ptr<RdmaEndPoint>` replaces raw pointer |
| #1907 | 2026-04-16 | FaultProxyTransport for deterministic fault injection testing |
| #1984 | 2026-04-27 | Rail recovery (cooldown + success-based) and failover E2E tests |

Architecture layers:

1. **Transport-internal retry** — TCP exponential backoff (`TcpParams`)
2. **Cross-transport failover** — RDMA → TCP, capped by `max_failover_attempts`
3. **Per-rail cooldown & recovery** — `RailMonitor` pauses/un-pauses individual (local, remote) NIC pairs

---

## P0 — Must Fix (Correctness & CI)

### 1. Fix batch `getTransferStatus` status overwrite bug

**Problem**: In the batch overload of `getTransferStatus`, when one task in the batch
triggers `resubmitTransferTask` successfully (re-marked PENDING), the aggregated
batch status may overwrite a genuinely FAILED task's status from earlier in the loop.

**Fix**: Track per-task terminal FAILED independently from tasks still retrying.
Aggregate as FAILED only if *all* remaining paths are exhausted for that task.

**Source**: Gemini code review on PR #1878.

### 2. Eliminate `std::string` machine_id from RDMA hot path

**Problem**: `RdmaSlice` stores `machine_id` as `std::string`. On the completion
polling path (`pollCompletion` → `RailMonitor::markRecovered/markFailed`), this
causes heap allocation and string copy per completion — unacceptable at millions
of completions/second.

**Fix**: Use an interned integer index or a stable `const char*` pointer from
`RailMonitor`'s pre-allocated machine ID table.

**Source**: Gemini code review on PR #1984.

### 3. Add TENT tests to CI

**Problem**: All failover tests run locally only (`USE_TENT=OFF` in CI). Regressions
can be merged without detection.

**Fix**: Add a GitHub Actions job:

```yaml
- cmake -S . -B build -DUSE_TENT=ON -DUSE_CUDA=OFF
- cmake --build build --target tent_failover_test tent_fault_proxy_test tent_rail_monitor_test tent_engine_failover_e2e_test -j
- ctest --test-dir build -R "^tent_" --output-on-failure
```

---

## P1 — Short Term

### 4. Expand fault injection (slowdown / bit-flip)

`FaultProxyTransport` already supports rate-based and deterministic failures.
Add:
- **Latency injection**: configurable delay before delegation simulates a degraded link.
- **Data corruption**: flip bytes in transfer buffers to test checksum paths (when added).

Requested by @alogfans in PR #1984 review.

### 5. Submit-stage failover

Current gap (see `failover.md` Known Gaps): when `submitTransferTasks` returns
non-OK, the task surfaces as FAILED without attempting the next transport. This
is the exact scenario in Issue #553 — Client-B uses TCP but the peer only
registered RDMA.

Requires either:
- A transport-level "atomic submit" capability flag, or
- Per-request status returned from `submitTransferTasks`.

### 6. Integrate with RDMA device scheduling (PR #1752)

@alogfans' QoS priority scheduler and congestion handling interacts with rail
cooldown. A paused rail must be excluded from the scheduler's candidate set.
Coordinate the merge window.

---

## P2 — Medium Term

### 7. Proactive health probing

Periodic RDMA ping / TCP keepalive to detect link degradation *before* a transfer
fails. Recovery signal feeds into `RailMonitor::markRecovered`.

### 8. Multi-transport segment metadata (Issue #553)

Allow a peer to register both RDMA and TCP transports in metadata. Connection
establishment prefers RDMA; falls back to TCP transparently. Cross-component
change: TE + Store + metadata.

### 9. TCP path stability (Bug #1986)

SIGSEGV in `MemcpyWorkerPool::workerThread` under concurrent TCP put/get.
Failover to TCP is pointless if TCP itself crashes.

### 10. Enhanced observability

Beyond `tent_transport_failover_total`:
- Per-rail cooldown duration histogram
- Failover latency (time from FAILED detection to successful resubmit)
- Recovery events counter (cooldown-expired vs success-based)
- Current healthy-rail gauge

---

## Execution Order

```
P0: batch status bug → hot-path string → CI integration
 ↓
P1: submit-stage failover (#553) → fault injection expansion → #1752 integration
 ↓
P2: health probing → TCP stability → multi-transport metadata → observability
```
