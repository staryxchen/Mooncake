import os
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from mooncake import pg


def worker(rank, world_size, results):
    torch.cuda.set_device(rank)
    dist.init_process_group(
        backend="mooncake",
        rank=rank,
        world_size=world_size,
        pg_options=pg.MooncakeBackendOptions(
            torch.zeros((world_size,), dtype=torch.int32, device="cuda")
        ),
    )

    # Warmup all_reduce to ensure all connections are established
    warmup = torch.ones(1, dtype=torch.float32, device="cuda")
    dist.all_reduce(warmup)

    if rank == 0:
        # Rank 0: send tensor to Rank 1
        tensor = torch.tensor([42, 43, 44, 45], dtype=torch.float32, device="cuda")
        dist.send(tensor, dst=1)
        results[rank] = tensor.tolist()
    else:
        # Rank 1: receive tensor from Rank 0
        tensor = torch.zeros(4, dtype=torch.float32, device="cuda")
        dist.recv(tensor, src=0)
        results[rank] = tensor.tolist()

    # Use all_reduce instead of barrier for sync (mooncake backend's barrier only supports CPU mode)
    sync_tensor = torch.ones(1, dtype=torch.float32, device="cuda")
    dist.all_reduce(sync_tensor)
    dist.destroy_process_group()


def main():
    world_size = 2
    assert (
        torch.cuda.device_count() >= world_size
    ), f"Requires at least {world_size} GPUs"

    os.environ["MASTER_ADDR"] = "127.0.0.1"
    os.environ["MASTER_PORT"] = "29501"

    mp_manager = mp.Manager()
    results = mp_manager.dict()

    mp.spawn(worker, args=(world_size, results), nprocs=world_size, join=True)

    print(f"Rank 0 sent: {results[0]}")
    print(f"Rank 1 received: {results[1]}")
    assert results[0] == results[1], "Sent and received data mismatch!"
    print("P2P send/recv test passed!")


if __name__ == "__main__":
    main()
