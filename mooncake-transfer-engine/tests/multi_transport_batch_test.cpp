// Copyright 2026 KVCache.AI
// Licensed under the Apache License, Version 2.0.
#include <gtest/gtest.h>
#include "multi_transport.h"
#include "transfer_metadata.h"

namespace mooncake {
class MultiTransportTestPeer {
   public:
    static void install(MultiTransport &multi, const std::string &name,
                        std::shared_ptr<Transport> transport) {
        multi.transport_map_[name] = std::move(transport);
    }
};
namespace {
class RecordingTransport : public Transport {
   public:
    std::vector<uint64_t> addresses;
    Status submitTransfer(BatchID,
                          const std::vector<TransferRequest> &) override {
        return Status::OK();
    }
    Status submitTransferTask(
        const std::vector<TransferTask *> &tasks) override {
        for (auto *task : tasks) {
            addresses.push_back(task->request->target_offset);
            task->is_finished = true;
        }
        return Status::OK();
    }
    Status getTransferStatus(BatchID, size_t, TransferStatus &) override {
        return Status::OK();
    }

   private:
    int registerLocalMemory(void *, size_t, const std::string &, bool,
                            bool) override {
        return 0;
    }
    int unregisterLocalMemory(void *, bool) override { return 0; }
    int registerLocalMemoryBatch(const std::vector<BufferEntry> &,
                                 const std::string &) override {
        return 0;
    }
    int unregisterLocalMemoryBatch(const std::vector<void *> &) override {
        return 0;
    }
    const char *getName() const override { return "recording"; }
};
class BatchRouting : public ::testing::Test {
   protected:
    std::string local = "local:1";
    std::shared_ptr<TransferMetadata> md =
        std::make_shared<TransferMetadata>(P2PHANDSHAKE);
    MultiTransport multi{md, local};
    std::shared_ptr<RecordingTransport> rdma =
        std::make_shared<RecordingTransport>();
    std::shared_ptr<RecordingTransport> tcp =
        std::make_shared<RecordingTransport>();
    void SetUp() override {
        MultiTransportTestPeer::install(multi, "rdma", rdma);
        MultiTransportTestPeer::install(multi, "tcp", tcp);
    }
    std::shared_ptr<TransferMetadata::SegmentDesc> add(uint64_t id,
                                                       std::string proto) {
        auto desc = std::make_shared<TransferMetadata::SegmentDesc>();
        desc->name = "remote:" + std::to_string(id);
        desc->protocol = proto;
        auto held = desc;
        md->addLocalSegment(id, desc->name, std::move(held));
        return desc;
    }
    Status submit(
        std::initializer_list<std::pair<uint64_t, uint64_t>> targets) {
        std::vector<Transport::TransferRequest> entries;
        for (auto [id, addr] : targets) {
            Transport::TransferRequest entry{};
            entry.target_id = id;
            entry.target_offset = addr;
            entry.length = 16;
            entries.push_back(entry);
        }
        auto batch = multi.allocateBatchID(entries.size());
        auto status = multi.submitTransfer(batch, entries);
        EXPECT_TRUE(multi.freeBatchID(batch).ok());
        return status;
    }
};
TEST_F(BatchRouting, RepeatedAndAlternatingTargetsReachCorrectTransport) {
    add(1, "rdma");
    add(2, "tcp");
    ASSERT_TRUE(
        submit({{1, 100}, {1, 200}, {2, 300}, {1, 400}, {2, 500}}).ok());
    EXPECT_EQ(rdma->addresses, (std::vector<uint64_t>{100, 200, 400}));
    EXPECT_EQ(tcp->addresses, (std::vector<uint64_t>{300, 500}));
}
TEST_F(BatchRouting, NextBatchObservesMetadataReplacement) {
    add(1, "rdma");
    ASSERT_TRUE(submit({{1, 100}, {1, 200}}).ok());
    add(1, "tcp");
    ASSERT_TRUE(submit({{1, 300}, {1, 400}}).ok());
    EXPECT_EQ(rdma->addresses.size(), 2u);
    EXPECT_EQ(tcp->addresses.size(), 2u);
}
TEST_F(BatchRouting, UnsupportedTargetDoesNotPartiallySubmit) {
    add(1, "rdma");
    add(2, "not-installed");
    ASSERT_FALSE(submit({{1, 100}, {2, 200}}).ok());
    EXPECT_TRUE(rdma->addresses.empty());
}
TEST_F(BatchRouting, TwoSidedFallbackIsRetained) {
    auto other = std::make_shared<RecordingTransport>();
    MultiTransport second(md, local);
    MultiTransportTestPeer::install(second, "rdma_twosided", other);
    add(1, "rdma");
    auto batch = second.allocateBatchID(2);
    Transport::TransferRequest request{};
    request.target_id = 1;
    request.length = 16;
    ASSERT_TRUE(second.submitTransfer(batch, {request, request}).ok());
    EXPECT_EQ(other->addresses.size(), 2u);
    EXPECT_TRUE(second.freeBatchID(batch).ok());
}
#ifdef ENABLE_MULTI_PROTOCOL
TEST_F(BatchRouting, MixedProtocolSameTargetStillRoutesByAddress) {
    auto desc = add(1, "rdma,tcp");
    TransferMetadata::BufferDesc a;
    a.addr = 100;
    a.length = 100;
    a.protocol = "rdma";
    TransferMetadata::BufferDesc b;
    b.addr = 300;
    b.length = 100;
    b.protocol = "tcp";
    desc->buffers = {a, b};
    ASSERT_TRUE(submit({{1, 100}, {1, 300}, {1, 116}, {1, 316}}).ok());
    EXPECT_EQ(rdma->addresses, (std::vector<uint64_t>{100, 116}));
    EXPECT_EQ(tcp->addresses, (std::vector<uint64_t>{300, 316}));
}
#endif
}  // namespace
}  // namespace mooncake
