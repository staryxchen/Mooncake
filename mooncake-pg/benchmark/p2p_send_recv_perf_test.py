import argparse
import os
import time

import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from mooncake import pg


def parse_args():
    parser = argparse.ArgumentParser(description="P2P send/recv performance test")
    parser.add_argument(
        "--size_bytes",
        type=int,
        default=None,
        help="Single message size in bytes. If not set, test 1KB to 8GB with x8 step.",
    )
    parser.add_argument(
        "--iters",
        type=int,
        default=1000,
        help="Iterations per size.",
    )
    return parser.parse_args()


def generate_sizes(size_bytes):
    if size_bytes is not None:
        return [size_bytes]
    sizes = []
    size = 1024
    max_size = 8 * 1024 * 1024 * 1024
    while size <= max_size:
        sizes.append(size)
        size *= 8
    return sizes


def format_size(size_bytes):
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    unit_idx = 0
    while value >= 1024.0 and unit_idx < len(units) - 1:
        value /= 1024.0
        unit_idx += 1
    if value.is_integer():
        return f"{int(value)}{units[unit_idx]}"
    return f"{value:.2f}{units[unit_idx]}"


def worker(rank, world_size, sizes, iters, results):
    torch.cuda.set_device(rank)
    # Set buffer capacity via env var, must be >= max test size
    max_size = max(sizes)
    buffer_cap = max(max_size, 16 * 1024 * 1024)
    os.environ["MC_P2P_BUFFER_CAP"] = str(buffer_cap)
    dist.init_process_group(
        backend="mooncake",
        rank=rank,
        world_size=world_size,
        pg_options=pg.MooncakeBackendOptions(
            torch.zeros((world_size,), dtype=torch.int32, device="cuda"),
        ),
    )

    warmup = torch.ones(1, dtype=torch.float32, device="cuda")
    dist.all_reduce(warmup)

    for size in sizes:
        tensor = torch.empty(size, dtype=torch.uint8, device="cuda")
        torch.cuda.synchronize()
        start = time.perf_counter()
        if rank == 0:
            for _ in range(iters):
                dist.send(tensor, dst=1)
        else:
            for _ in range(iters):
                dist.recv(tensor, src=0)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        avg_ms = (elapsed * 1000.0) / iters
        bw_gbps = 0.0
        if avg_ms > 0:
            bw_gbps = (size / (avg_ms / 1000.0)) / 1e9
        results[f"{size}_rank{rank}"] = (avg_ms, bw_gbps)

        sync_tensor = torch.ones(1, dtype=torch.float32, device="cuda")
        dist.all_reduce(sync_tensor)

    dist.destroy_process_group()


def main():
    args = parse_args()
    world_size = 2
    assert (
        torch.cuda.device_count() >= world_size
    ), f"Requires at least {world_size} GPUs"

    os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
    os.environ.setdefault("MASTER_PORT", "29501")

    sizes = generate_sizes(args.size_bytes)

    mp_manager = mp.Manager()
    results = mp_manager.dict()

    mp.spawn(
        worker,
        args=(world_size, sizes, args.iters, results),
        nprocs=world_size,
        join=True,
    )

    for size in sizes:
        send_avg_ms, send_bw_gbps = results[f"{size}_rank0"]
        recv_avg_ms, recv_bw_gbps = results[f"{size}_rank1"]
        print(
            "size={size} bytes={bytes} send_avg_lat={send_ms:.3f}ms send_bw={send_bw:.2f}GB/s "
            "recv_avg_lat={recv_ms:.3f}ms recv_bw={recv_bw:.2f}GB/s".format(
                size=format_size(size),
                bytes=size,
                send_ms=send_avg_ms,
                send_bw=send_bw_gbps,
                recv_ms=recv_avg_ms,
                recv_bw=recv_bw_gbps,
            )
        )


if __name__ == "__main__":
    main()
