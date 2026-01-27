import os
import torch
import torch.distributed as dist
from mooncake import pg


def worker(rank, world_size, local_rank):
    torch.cuda.set_device(local_rank)
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

    rank = dist.get_rank()
    backend = dist.get_backend()
    print(f"Rank {rank} is ready and using backend {backend}")

    if rank == 0:
        # Rank 0: send tensor to Rank 1
        tensor = torch.tensor([42, 43, 44, 45], dtype=torch.float32, device="cuda")
        dist.send(tensor, dst=1)
        print(f"Rank 0 sent: {tensor.tolist()}")
    else:
        # Rank 1: receive tensor from Rank 0
        tensor = torch.zeros(4, dtype=torch.float32, device="cuda")
        dist.recv(tensor, src=0)
        received = tensor.tolist()
        print(f"Rank 1 received: {received}")
        assert received == [42.0, 43.0, 44.0, 45.0], "Sent and received data mismatch!"

    # Use all_reduce instead of barrier for sync (mooncake backend's barrier only supports CPU mode)
    sync_tensor = torch.ones(1, dtype=torch.float32, device="cuda")
    dist.all_reduce(sync_tensor)
    dist.destroy_process_group()


def main():
    world_size = int(os.environ.get("WORLD_SIZE", "2"))
    rank = int(os.environ["RANK"])
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))

    os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
    os.environ.setdefault("MASTER_PORT", "29501")

    worker(rank, world_size, local_rank)

    if rank == 1:
        print("P2P send/recv test passed!")


if __name__ == "__main__":
    main()
