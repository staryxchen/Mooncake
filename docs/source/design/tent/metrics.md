# TENT Metrics System

TENT provides a built-in metrics system based on yalantinglibs, compatible with Prometheus for monitoring data transfer performance and system health.

## Overview

The metrics system supports three metric types:

- **Counter**: Monotonically increasing values (e.g., total bytes transferred, total requests)
- **Histogram**: Distribution of values with configurable buckets (e.g., latency)
- **Gauge**: Current state values maintained through paired add/sub operations (e.g., in-flight attempts)

All metrics are thread-safe and designed for high-performance data paths. Hot-path metrics record through pre-resolved label cells (`cached_metric.h`): each label's atomic cell is resolved once and cached, so the steady-state update is a relaxed atomic add with no locks. Measured with tebench 4K/1, the throughput gap between metrics enabled and disabled stays under 5% across thread counts.

## Performance Optimization

The metrics system provides two levels of control for performance optimization:

### Compile-time Disable (Zero Overhead)

By default, metrics are **disabled** at compile time for maximum performance. To enable metrics, build with:

```bash
cmake -DTENT_METRICS_ENABLED=ON ..
```

When disabled at compile time (`TENT_METRICS_ENABLED=OFF`, the default), all metrics macros expand to `((void)0)` and the `recordTaskCompletionMetrics` body is `#if`-gated out, resulting in **zero runtime overhead** on the transfer hot path.

### Runtime Disable (Minimal Overhead)

When metrics are enabled at compile time, you can still disable them at runtime:

```cpp
// Disable metrics collection at runtime
TentMetrics::setEnabled(false);

// Re-enable metrics collection
TentMetrics::setEnabled(true);

// Check current state
bool enabled = TentMetrics::isEnabled();
```

When disabled at runtime, counter and histogram record functions return immediately after a single atomic load (~1ns overhead). State gauges (`tent_inflight_attempts`, `tent_registered_buffer_bytes`) are the exception: they keep updating while disabled, because their paired add/sub operations must stay symmetric — skipping one half of a pair across a `setEnabled()` transition would permanently corrupt the gauge. The runtime switch therefore means "stop sampling": counters and histograms freeze, state tracking stays live.

## Configuration

### Configuration Sources (Priority Order)

1. **Config File** (highest priority)
2. **Environment Variables** (medium priority)
3. **Default Values** (lowest priority)

### Config File Format

TENT metrics configuration is integrated into the main `transfer-engine.json` configuration file:

```json
{
  "local_segment_name": "",
  "metadata_type": "p2p",
  "metadata_servers": "127.0.0.1:2379",
  "log_level": "warning",
  "metrics": {
    "enabled": true,
    "http_port": 9100,
    "http_host": "0.0.0.0",
    "http_server_threads": 2,
    "report_interval_seconds": 30
  },
  "transports": {
    // ... transport configuration
  }
}
```

**Note**:
- `report_interval_seconds`: Set to 0 to disable periodic logging
- Histogram buckets are fixed at compile time (see `kLatencyBuckets` / `kSizeBuckets` in `tent_metrics.h`) for reproducible observability across deployments.

### Environment Variables

```bash
# Basic settings
TENT_METRICS_ENABLED=true
TENT_METRICS_HTTP_PORT=9100
TENT_METRICS_HTTP_HOST=0.0.0.0
TENT_METRICS_HTTP_SERVER_THREADS=2
TENT_METRICS_REPORT_INTERVAL=30  # Set to 0 to disable periodic logging
```

## Quick Start

### Build with Metrics Enabled

```bash
# Enable metrics at compile time (disabled by default)
cmake -DTENT_METRICS_ENABLED=ON ..
make
```

### Basic Usage

```cpp
#include "tent/metrics/tent_metrics.h"
#include "tent/metrics/config_loader.h"

// Load configuration from transfer-engine.json
auto config = MetricsConfigLoader::loadWithDefaults();

// Initialize TENT metrics system
auto& tent_metrics = TentMetrics::instance();
tent_metrics.initialize(config);

// HTTP server starts automatically
```

### Recording Transfer Metrics

```cpp
// Using convenience macros (recommended)
TENT_RECORD_READ_COMPLETED(RDMA, 1024*1024, 0.025);   // 1MB read in 25ms
TENT_RECORD_WRITE_COMPLETED(RDMA, 512*1024, 0.015);    // 512KB write in 15ms
TENT_RECORD_READ_FAILED(TCP);                           // read failed (no bytes recorded)
TENT_RECORD_WRITE_FAILED(TCP);                          // write failed (no bytes recorded)
TENT_RECORD_TRANSPORT_FAILOVER(RDMA, TCP);              // cross-transport failover event

// Direct API usage
auto& tent_metrics = TentMetrics::instance();
tent_metrics.recordReadCompleted(RDMA, 1024*1024, 0.025);
tent_metrics.recordWriteCompleted(RDMA, 512*1024, 0.015);
tent_metrics.recordReadFailed(TCP);
tent_metrics.recordWriteFailed(TCP);
tent_metrics.recordTransportFailover(RDMA, TCP);

// Deadline feasibility (RFC #2519, observability only):
tent_metrics.recordDeadlineMLU(RDMA, 0.8);        // MLU < 1 met the deadline
tent_metrics.recordDeadlineInfeasible(TCP);        // deadline was in the past at submit

// Causal-chain per-stage latency breakdown (microseconds):
tent_metrics.recordStageLatency(TentMetrics::Stage::QueueWait, RDMA, 12.0);
tent_metrics.recordStageLatency(TentMetrics::Stage::Dispatch, RDMA, 45.0);
tent_metrics.recordStageLatency(TentMetrics::Stage::Transport, RDMA, 130.0);
```

### RAII Latency Measurement

```cpp
// Automatic latency measurement using RAII
{
    TENT_SCOPED_READ_LATENCY(RDMA, 1024 * 1024); // e.g. 1MB
    // ... perform read operation ...
}  // latency automatically recorded when scope exits

{
    TENT_SCOPED_WRITE_LATENCY(RDMA, 512 * 1024); // e.g. 512KB
    // ... perform write operation ...
}
```

## HTTP Server Endpoints

The HTTP server provides multiple endpoints:

- **`/metrics`**: Prometheus format
- **`/metrics/summary`**: Human-readable summary
- **`/metrics/json`**: JSON format
- **`/health`**: Health check endpoint

### Example Responses

**Prometheus Format (`/metrics`)**:
```
# HELP tent_read_bytes_total Total bytes read via TENT
# TYPE tent_read_bytes_total counter
tent_read_bytes_total{transport="rdma"} 1048576
tent_read_bytes_total{transport="tcp"} 524288

# HELP tent_write_bytes_total Total bytes written via TENT
# TYPE tent_write_bytes_total counter
tent_write_bytes_total{transport="rdma"} 524288

# HELP tent_read_requests_total Total read requests via TENT
# TYPE tent_read_requests_total counter
tent_read_requests_total{transport="rdma"} 100
tent_read_requests_total{transport="tcp"} 50

# HELP tent_read_failures_total Total read failures via TENT
# TYPE tent_read_failures_total counter
tent_read_failures_total{transport="tcp"} 2

# HELP tent_transport_failover_total Total cross-transport failover events
# TYPE tent_transport_failover_total counter
tent_transport_failover_total{from="rdma",to="tcp"} 1

# HELP tent_task_failures_total Task-level failures by terminal transport and failure reason
# TYPE tent_task_failures_total counter
tent_task_failures_total{transport="rdma",reason="submit"} 2

# HELP tent_inflight_attempts In-flight transport attempts (submitted, not yet finished)
# TYPE tent_inflight_attempts gauge
tent_inflight_attempts{transport="rdma"} 3

# HELP tent_registered_buffer_bytes Registered local buffer bytes
# TYPE tent_registered_buffer_bytes gauge
tent_registered_buffer_bytes{transport="rdma"} 1073741824

# HELP tent_read_latency_us Read latency distribution in microseconds
# TYPE tent_read_latency_us histogram
tent_read_latency_us_bucket{transport="rdma",le="100"} 10
tent_read_latency_us_bucket{transport="rdma",le="500"} 50
...
```

**JSON Format (`/metrics/json`)**: counters are aggregated across labels into flat numbers; histograms are objects with `count`/`sum`/`buckets`; gauges are aggregated across labels into flat numbers (per-transport breakdown is via the Prometheus endpoint).
```json
{
  "tent_read_bytes_total": 1048576,
  "tent_write_bytes_total": 524288,
  "tent_read_requests_total": 100,
  "tent_write_requests_total": 50,
  "tent_read_failures_total": 2,
  "tent_write_failures_total": 1,
  "tent_transport_failover_total": 1,
  "tent_task_failures_total": 2,
  "tent_inflight_attempts": 3,
  "tent_registered_buffer_bytes": 1073741824,
  "tent_read_latency_us": {
    "count": 100,
    "sum": 2500,
    "buckets": {"100": 10, "500": 50, "...": "..."}
  }
}
```

**Summary Format (`/metrics/summary`)**:
```
Read: 1.00 MB (100 reqs, 2 fails) | Write: 512.00 KB (50 reqs, 1 fails) | Failovers: 1 | Quarantined batches: 0
```

## Available Metrics

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `tent_read_bytes_total` | Counter | `transport` | Total bytes read via TENT (success only; failures record no bytes) |
| `tent_write_bytes_total` | Counter | `transport` | Total bytes written via TENT (success only) |
| `tent_read_requests_total` | Counter | `transport` | Total read requests via TENT (success + failure) |
| `tent_write_requests_total` | Counter | `transport` | Total write requests via TENT (success + failure) |
| `tent_read_failures_total` | Counter | `transport` | Total read failures via TENT |
| `tent_write_failures_total` | Counter | `transport` | Total write failures via TENT |
| `tent_transport_failover_total` | Counter | `from`, `to` | Total cross-transport failover events |
| `tent_transport_attempts_total` | Counter | `transport`, `operation` | Physical transport attempts submitted for execution |
| `tent_transport_attempt_failures_total` | Counter | `transport`, `operation` | Physical transport attempts that terminated with `FAILED` |
| `tent_task_failures_total` | Counter | `transport`, `reason` | Task-level failures by terminal transport and failure reason |
| `tent_deadline_infeasible_total` | Counter | `transport` | Transfers whose deadline was already in the past at submit time |
| `tent_read_latency_us` | Histogram | `transport` | Read latency distribution in microseconds |
| `tent_write_latency_us` | Histogram | `transport` | Write latency distribution in microseconds |
| `tent_read_size_bytes` | Histogram | `transport` | Read request size distribution in bytes |
| `tent_write_size_bytes` | Histogram | `transport` | Write request size distribution in bytes |
| `tent_deadline_mlu_permille` | Histogram | `transport` | Deadline feasibility ratio (MLU x 1000); 1000 = MLU 1.0 (the met/missed boundary) |
| `tent_stage_queue_wait_us` | Histogram | `transport` | Causal chain: queue wait latency in microseconds |
| `tent_stage_dispatch_us` | Histogram | `transport` | Causal chain: dispatch latency in microseconds |
| `tent_stage_transport_us` | Histogram | `transport` | Causal chain: transport execution latency in microseconds |
| `tent_transport_attempt_latency_us` | Histogram | `transport`, `operation` | Observed latency of each physical transport attempt |
| `tent_inflight_attempts` | Gauge | `transport` | In-flight transport attempts (submitted, not yet finished) |
| `tent_registered_buffer_bytes` | Gauge | `transport` | Registered local buffer bytes |

**Notes**:
- `*_requests_total` counts terminal logical/merged-transfer outcomes. Its `transport` label is the **final transport**: a request recovered by RDMA→TCP failover is counted once under `tcp`.
- `tent_transport_attempts_total` and `tent_transport_attempt_failures_total` measure physical transport reliability. A recovered RDMA→TCP request contributes one failed RDMA attempt and one successful TCP attempt.
- Attempt latency currently ends when polling or the progress worker observes the terminal status, so it may include completion-observation delay.
- `*_failures_total` does not record bytes; failed transfers transfer no bytes.
- `tent_deadline_infeasible_total` is a dedicated counter (not a histogram sentinel) so infeasible-at-submit cases are distinguishable from genuine high-MLU samples.
- `tent_task_failures_total` attributes the **root cause**: `TaskInfo::failure_stage` marks where the first failure originated (submit rejection vs poll observation, first-set-wins), so a submit failure followed by a successful failover and a later poll failure still counts as `reason="submit"`. Its `transport` label uses the transport captured at attempt start (`attempt_type`), so failures are attributed correctly even when `task.type` has been reset. Unlike the legacy `*_failures_total` counters, it also records `TIMEOUT` and `CANCELED` outcomes.
- Gauges track engine state, not samples: they keep updating across `setEnabled()` transitions (see Runtime Disable above). `tent_inflight_attempts` persistently high or stuck is the signature of a stalled pipeline.
- Label cells are created lazily on first record: a metric (including gauge series) only appears in the scrape output after its first event — no failures means no `tent_task_failures_total` series at all. `absent()`-style Prometheus alerts do not apply; alert on values (`> 0`) instead, and use endpoint liveness (connection refused vs 200-with-empty-body) to distinguish a dead endpoint from an idle engine.
- yalantinglibs omits zero-valued counters/histograms from the Prometheus output, so a metric only appears once it has been observed at least once.

### Labels

Transfer and attempt metrics carry a `transport` label (the
`tent_transport_failover_total` counter uses `from` and `to` instead) so they
can be sliced by transport without grepping logs. Attempt metrics also carry
an `operation` label; task failure metrics carry a `reason` label. Label
values come exclusively from the `TransportType` enum closed set — no
arbitrary transport strings are accepted.

| Label | Values | Description |
|-------|--------|-------------|
| `transport` | `unspec`, `rdma`, `mnnvl`, `shm`, `nvlink`, `gds`, `io_uring`, `tcp`, `ascend`, `sunrise_link`, `tpu` | The transport that handled the transfer |
| `operation` | `read`, `write` | Attempt operation |
| `reason` | `submit`, `poll`, `timeout`, `canceled` | Where the task failure originated (root cause) |
| `from` | (same set) | Transport that failed before failover |
| `to` | (same set) | Transport that the failover switched to |

Transport label values come from the shared `transportTypeName()` mapping.
`unspec` covers transfers that failed before a transport was selected.

**Cardinality**: the `transport` label has 11 values; the failover
`from`/`to` pair has at most 11x11 = 121 combinations (in practice only a
few pairs ever occur), each attempt metric has at most 11x2 = 22
transport/operation combinations, and task failures have at most 11x4 = 44
transport/reason combinations. Total series across all metrics is bounded
at ~1500.

## Integration with TransferEngine

The metrics system is automatically integrated with TransferEngine. When TransferEngine starts, it loads the metrics config from its own configuration chain, validates it, and initializes the metrics system (see `TransferEngineImpl::setup`):

```cpp
#include "tent/metrics/tent_metrics.h"
#include "tent/metrics/config_loader.h"

// Passing the engine's Config lets metrics keys come from the same
// transfer-engine.json / environment chain as the rest of the engine.
auto metrics_config = MetricsConfigLoader::loadWithDefaults(conf.get());
if (metrics_config.enabled) {
    std::string error;
    if (MetricsConfigLoader::validateConfig(metrics_config, &error)) {
        TentMetrics::instance().initialize(metrics_config);
    }
}
```

Metrics are automatically recorded at the TENT layer:

- **Latency tracking**: Start time is recorded when `submitTransfer` is called
- **Metrics recording**: When `getTransferStatus` detects task completion, latency is calculated and metrics are recorded
- **Attempt tracking**: Each concrete `Transport::submitTransferTasks()` call is counted as one attempt. A failed attempt is closed before failover changes the task's current transport, and the replacement attempt gets a fresh attempt timestamp. Synchronous submit failures are also closed as failed attempts. The transport is captured when the attempt starts, so it is attributed correctly even if failover overwrites the task's current transport afterwards. Staging is an orchestration step, not a transport attempt: `ProxyManager` chunks the transfer and issues the real transport submissions, which are the ones counted, so a staged transfer is not double-counted.
- **Failure attribution**: `TaskInfo::failure_stage` marks where a task's first failure originated — at the submit-rejection sites (before any failover recovery attempt) or at poll observation, first-set-wins — and `tent_task_failures_total` records the terminal outcome with that root cause.
- **State gauges**: In-flight attempts are incremented on attempt submit and decremented on attempt finish; registered buffer bytes are maintained on register/unregisterLocalMemory from the transports that actually registered each buffer.

This provides two complementary views:

- Logical request latency and outcome, attributed to the final transport for backward compatibility.
- Physical attempt count, failure count, and latency, attributed to the transport that actually executed that attempt.

For a recovered RDMA→TCP request, request metrics record one successful TCP
outcome, while attempt metrics record one failed RDMA attempt and one successful
TCP attempt. The causal-chain stage metrics (`tent_stage_queue_wait_us`,
`tent_stage_dispatch_us`, `tent_stage_transport_us`) are unchanged by this
addition: they remain attributed to the final transport and
`tent_stage_transport_us` still spans the whole request, so existing dashboards
keep their meaning. Use `tent_transport_attempt_latency_us` (labeled by the
transport that actually ran each attempt) to inspect per-attempt latency in a
multi-attempt request.

**Note**: Remember to build with `-DTENT_METRICS_ENABLED=ON` to enable metrics collection.

## Adding New Metrics

Hot-path metrics are backed by cached label cells (`tent/include/tent/metrics/cached_metric.h`): the label domain must be a compile-time-known enum (like `TransportType`), each label value maps to a stable slot index, and the steady-state update is a relaxed atomic add on the pre-resolved cell — no lock, no hashing, no string construction.

### Step 1: Declare the Metric

Add the metric member variable in `tent_metrics.h`:

```cpp
// CachedDynamicCounter<N>: N = label arity.
// label_domain_size bounds the slot index space (number of distinct label
// values that will ever be recorded).
metrics::CachedDynamicCounter<1> new_counter_{
    "tent_new_counter_total", "Description of the counter",
    kTransportLabel, kTransportDomain};

// CachedDynamicHistogram<N>: composed of cached-cell bucket counters plus
// the sum counter; bucket boundaries are inclusive upper bounds with a
// trailing +Inf bucket.
metrics::CachedDynamicHistogram<1> new_histogram_{
    "tent_new_histogram_us", "Description", kMyBuckets,
    kTransportLabel, kTransportDomain};
```

Also add a static slot helper so callers map an enum to a stable index:

```cpp
static size_t newCounterSlot(TransportType tp) {
    return static_cast<size_t>(tp);
}
```

### Step 2: Register the Metric

Add the metric pointer to `registerMetrics()` in `tent_metrics.cpp`:

```cpp
void TentMetrics::registerMetrics() {
    counters_ = {
        &read_bytes_total_,
        // ... existing counters ...
        &new_counter_,  // counters serialize via the standard path
    };

    histograms_ = {
        &read_latency_,
        // ... existing histograms ...
        &new_histogram_,
    };

    // Gauges (cached cells used with paired add/sub) serialize through
    // serializeGaugePrometheus() instead — do NOT add them to counters_.
    gauges_ = {
        &inflight_attempts_,
        // ...
    };
}
```

### Step 3: Add Recording Methods

Add public methods that resolve the cell lazily and update it:

```cpp
// In tent_metrics.h:
void recordNewMetric(TransportType tp, int64_t value);

// In tent_metrics.cpp:
void TentMetrics::recordNewMetric(TransportType tp, int64_t value) {
    if (!initialized_ || !runtime_enabled_.load(std::memory_order_relaxed))
        return;
    new_counter_.incCached(newCounterSlot(tp), [tp] {
        return std::array<std::string, 1>{transportTypeName(tp)};
    }, value);
}
```

The label lambda only runs on the first use of a slot (cache miss); the
steady state is a relaxed `fetch_add`. For histograms use `observeCached()`.
For state gauges, use paired `incCached(..., +1)` / `incCached(..., -1)`
calls that ignore `runtime_enabled_` (see Runtime Disable above), and add
matching stubs to the `#else` (compile-time disabled) section.

### Automatic Serialization

Once registered in `registerMetrics()`, the new metric will be **automatically included** in:
- `/metrics` (Prometheus format)
- `/metrics/json` (JSON format)

No changes to `getPrometheusMetrics()` or `getJsonMetrics()` are required.

## Advanced Configuration

### Histogram Buckets

Histogram bucket boundaries are fixed at compile time (defined as `static inline const std::vector<double>` members in `tent_metrics.h`: `kLatencyBuckets`, `kSizeBuckets`, `kMluPerMilleBuckets`, `kStageBuckets`). They are intentionally not runtime-configurable so that observability is reproducible across deployments. To change buckets, edit the constant in `tent_metrics.h` and rebuild.

### Compatibility

The `metrics/latency_buckets` and `metrics/size_buckets` config keys, and the `TENT_METRICS_LATENCY_BUCKETS` / `TENT_METRICS_SIZE_BUCKETS` environment variables, were removed and are now silently ignored. Histogram buckets are fixed at compile time (see Histogram Buckets above).

**Migration:** remove these keys from your `transfer-engine.json` and unset the environment variables. If custom bucket boundaries are required, edit `kLatencyBuckets` / `kSizeBuckets` in `tent/metrics/tent_metrics.h` and rebuild.

> **Note:** the previous runtime-configurable implementation had a latent bug — the JSON output labeled custom buckets with the compile-time default boundaries, so `/metrics/json` was already mislabeled for custom-bucket deployments. Removing the knob fixes this rather than preserving a buggy code path.

The `metrics/enable_prometheus` and `metrics/enable_json` config keys, and the `TENT_METRICS_ENABLE_PROMETHEUS` / `TENT_METRICS_ENABLE_JSON` environment variables, were removed and are now silently ignored. The `/metrics` and `/metrics/json` HTTP endpoints are now registered unconditionally and cannot be toggled independently — both are read-only and served on the same port, so there was no real scenario where a deployment wanted one disabled.

**Migration:** remove these keys from your `transfer-engine.json` and unset the environment variables. To fully disable the metrics subsystem (no HTTP server, no periodic summary logging, no recording), set `metrics/enabled` to `false` (or `TENT_METRICS_ENABLED=false`). Note that "log-only mode" — where periodic summaries are still logged but `/metrics` is unavailable — only occurs when metrics are enabled but the HTTP port cannot be bound (e.g. port conflict); it is not triggered by `metrics/enabled=false`.

### Validation

```cpp
MetricsConfig config = MetricsConfigLoader::loadWithDefaults();
std::string error_msg;
if (!MetricsConfigLoader::validateConfig(config, &error_msg)) {
    LOG(ERROR) << "Invalid metrics config: " << error_msg;
    return;
}
```

## Prometheus Integration

### Prometheus Configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'tent-metrics'
    static_configs:
      - targets: ['localhost:9100']
    scrape_interval: 15s
    metrics_path: /metrics
```

### Grafana Queries

```promql
# Transfer throughput (MB/s) — all transports
rate(tent_read_bytes_total[5m]) / 1024 / 1024
rate(tent_write_bytes_total[5m]) / 1024 / 1024

# Transfer throughput (MB/s) — per transport
rate(tent_read_bytes_total{transport="rdma"}[5m]) / 1024 / 1024
rate(tent_read_bytes_total{transport="tcp"}[5m]) / 1024 / 1024

# Request rate
rate(tent_read_requests_total[5m])
rate(tent_write_requests_total[5m])

# Failure rate (all transports)
rate(tent_read_failures_total[5m]) / rate(tent_read_requests_total[5m])

# Failure rate (per transport)
rate(tent_read_failures_total{transport="tcp"}[5m]) / rate(tent_read_requests_total{transport="tcp"}[5m])

# P99 latency (note: latency is in microseconds, convert to seconds for display)
histogram_quantile(0.99, rate(tent_read_latency_us_bucket[5m])) / 1000000
histogram_quantile(0.99, rate(tent_write_latency_us_bucket[5m])) / 1000000

# P99 latency per transport
histogram_quantile(0.99, rate(tent_read_latency_us_bucket{transport="rdma"}[5m])) / 1000000

# Failover rate by transport pair
rate(tent_transport_failover_total{from="rdma",to="tcp"}[5m])

# RDMA physical-attempt failure rate
sum(rate(tent_transport_attempt_failures_total{transport="rdma"}[5m]))
/
sum(rate(tent_transport_attempts_total{transport="rdma"}[5m]))

# Task failure rate by reason (root cause: submit rejection vs in-flight failure)
sum by (reason) (rate(tent_task_failures_total[5m]))

# In-flight attempts — a persistently high or stuck value signals a stalled
# pipeline
tent_inflight_attempts

# Alert: any task failure at all (values, not series presence — see lazy
# registration note above)
sum(rate(tent_task_failures_total[5m])) > 0

# P99 latency of RDMA write attempts
histogram_quantile(
  0.99,
  sum by (le) (
    rate(tent_transport_attempt_latency_us_bucket{
      transport="rdma",operation="write"
    }[5m])
  )
) / 1000000
```
