#include "lol_assistant/storage/async_storage_writer.h"

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <type_traits>
#include <utility>

#include "lol_assistant/storage/jsonl_writer.h"
#include "lol_assistant/storage/session_store.h"

namespace lol_assistant::storage {

struct AsyncStorageWriter::Impl final {
  struct QueuedCommand final {
    StorageCommand command;
    StoragePriority priority;
  };

  std::unique_ptr<SessionStore> store{};
  std::unique_ptr<JsonlWriter> jsonl_writer{};
  std::size_t maximum_queue_size{0U};

  mutable std::mutex queue_mutex{};
  std::condition_variable queue_changed{};
  std::deque<QueuedCommand> queue{};
  bool accepting{true};
  bool stop_requested{false};
  bool shutdown_complete{false};
  StorageStatus shutdown_status{};
  std::thread worker{};

  std::atomic<std::uint64_t> accepted{0U};
  std::atomic<std::uint64_t> processed{0U};
  std::atomic<std::uint64_t> failed{0U};
  std::atomic<std::uint64_t> rejected_full{0U};
  std::atomic<std::uint64_t> rejected_contention{0U};
  std::atomic<std::uint64_t> rejected_closed{0U};
  std::atomic<std::uint64_t> dropped_lower_priority{0U};

  mutable std::mutex error_mutex{};
  std::string last_error{};

  [[nodiscard]] StorageStatus Process(const StorageCommand& command) {
    return std::visit(
        [this](const auto& record) -> StorageStatus {
          using Record = std::decay_t<decltype(record)>;
          if constexpr (std::is_same_v<Record, SessionRecord>) {
            return store->CreateSession(record);
          } else if constexpr (std::is_same_v<Record, SessionEndRecord>) {
            return store->EndSession(record);
          } else if constexpr (std::is_same_v<Record, AugmentOfferRecord>) {
            return store->InsertAugmentOffer(record);
          } else if constexpr (std::is_same_v<Record, AugmentChoiceRecord>) {
            return store->InsertAugmentChoice(record);
          } else if constexpr (std::is_same_v<Record,
                                              RecognitionResultRecord>) {
            return store->InsertRecognitionResult(record);
          } else if constexpr (std::is_same_v<Record, ArtifactRecord>) {
            return store->InsertArtifact(record);
          } else {
            if (jsonl_writer == nullptr) {
              return {StorageErrorCode::InvalidArgument, 0,
                      "JSONL command received without a configured JSONL path"};
            }
            return jsonl_writer->Write(record);
          }
        },
        command);
  }

  void RecordFailure(const StorageStatus& status) {
    failed.fetch_add(1U, std::memory_order_relaxed);
    std::scoped_lock lock{error_mutex};
    last_error = status.message;
  }

  void Run() {
    for (;;) {
      QueuedCommand next{SessionRecord{}, StoragePriority::Normal};
      {
        std::unique_lock lock{queue_mutex};
        queue_changed.wait(lock, [this] {
          return stop_requested || !queue.empty();
        });
        if (queue.empty()) {
          if (stop_requested) {
            break;
          }
          continue;
        }
        next = std::move(queue.front());
        queue.pop_front();
      }

      const auto status = Process(next.command);
      if (status.IsSuccess()) {
        processed.fetch_add(1U, std::memory_order_relaxed);
      } else {
        RecordFailure(status);
      }
    }
  }
};

AsyncStorageWriter::AsyncStorageWriter(
    std::unique_ptr<Impl> implementation) noexcept
    : impl_(std::move(implementation)) {}

AsyncStorageWriter::~AsyncStorageWriter() {
  if (impl_ != nullptr) {
    (void)Shutdown();
  }
}

StorageStatus AsyncStorageWriter::Start(
    const AsyncStorageOptions& options,
    std::unique_ptr<AsyncStorageWriter>& writer) {
  writer.reset();
  if (options.maximum_queue_size == 0U) {
    return {StorageErrorCode::InvalidArgument, 0,
            "maximum queue size must be greater than zero"};
  }

  auto implementation = std::make_unique<Impl>();
  implementation->maximum_queue_size = options.maximum_queue_size;
  auto status = SessionStore::Open(options.database_path, options.workspace_root,
                                   implementation->store,
                                   options.busy_timeout);
  if (!status.IsSuccess()) {
    return status;
  }
  if (options.jsonl_path.has_value()) {
    status = JsonlWriter::Open(*options.jsonl_path, options.workspace_root,
                               implementation->jsonl_writer);
    if (!status.IsSuccess()) {
      return status;
    }
  }

  auto result = std::unique_ptr<AsyncStorageWriter>(
      new AsyncStorageWriter(std::move(implementation)));
  try {
    result->impl_->worker = std::thread([worker = result->impl_.get()] {
      worker->Run();
    });
  } catch (const std::exception& error) {
    return {StorageErrorCode::OpenFailed, 0,
            std::string{"failed to start storage worker: "} + error.what()};
  }
  writer = std::move(result);
  return StorageStatus::Ok();
}

EnqueueResult AsyncStorageWriter::TryEnqueue(StorageCommand command,
                                             const StoragePriority priority) {
  if (impl_ == nullptr) {
    return EnqueueResult::RejectedClosed;
  }
  std::unique_lock lock{impl_->queue_mutex, std::try_to_lock};
  if (!lock.owns_lock()) {
    impl_->rejected_contention.fetch_add(1U, std::memory_order_relaxed);
    return EnqueueResult::RejectedContention;
  }
  if (!impl_->accepting) {
    impl_->rejected_closed.fetch_add(1U, std::memory_order_relaxed);
    return EnqueueResult::RejectedClosed;
  }

  bool dropped = false;
  if (impl_->queue.size() >= impl_->maximum_queue_size) {
    const auto candidate = std::find_if(
        impl_->queue.begin(), impl_->queue.end(),
        [priority](const Impl::QueuedCommand& queued) {
          return static_cast<std::uint8_t>(queued.priority) <
                 static_cast<std::uint8_t>(priority);
        });
    if (candidate == impl_->queue.end()) {
      impl_->rejected_full.fetch_add(1U, std::memory_order_relaxed);
      return EnqueueResult::RejectedFull;
    }
    impl_->queue.erase(candidate);
    impl_->dropped_lower_priority.fetch_add(1U, std::memory_order_relaxed);
    dropped = true;
  }

  impl_->queue.push_back(Impl::QueuedCommand{std::move(command), priority});
  impl_->accepted.fetch_add(1U, std::memory_order_relaxed);
  lock.unlock();
  impl_->queue_changed.notify_one();
  return dropped ? EnqueueResult::AcceptedAfterDroppingLowerPriority
                 : EnqueueResult::Accepted;
}

StorageStatus AsyncStorageWriter::Shutdown() {
  if (impl_ == nullptr) {
    return StorageStatus::Ok();
  }
  {
    std::scoped_lock lock{impl_->queue_mutex};
    if (impl_->shutdown_complete) {
      return impl_->shutdown_status;
    }
    impl_->accepting = false;
    impl_->stop_requested = true;
  }
  impl_->queue_changed.notify_all();
  if (impl_->worker.joinable()) {
    impl_->worker.join();
  }

  StorageStatus status = StorageStatus::Ok();
  if (impl_->jsonl_writer != nullptr) {
    const auto flush_status = impl_->jsonl_writer->Flush();
    const auto close_status = impl_->jsonl_writer->Close();
    if (!flush_status.IsSuccess()) {
      status = flush_status;
    } else if (!close_status.IsSuccess()) {
      status = close_status;
    }
  }
  if (impl_->store != nullptr) {
    const auto close_status = impl_->store->Close();
    if (status.IsSuccess() && !close_status.IsSuccess()) {
      status = close_status;
    }
  }
  if (status.IsSuccess() &&
      impl_->failed.load(std::memory_order_relaxed) != 0U) {
    status = {StorageErrorCode::IoError, 0,
              "one or more accepted storage commands failed: " + LastError()};
  }

  {
    std::scoped_lock lock{impl_->queue_mutex};
    impl_->shutdown_status = status;
    impl_->shutdown_complete = true;
  }
  return status;
}

AsyncStorageStats AsyncStorageWriter::Stats() const noexcept {
  if (impl_ == nullptr) {
    return {};
  }
  AsyncStorageStats stats;
  stats.accepted = impl_->accepted.load(std::memory_order_relaxed);
  stats.processed = impl_->processed.load(std::memory_order_relaxed);
  stats.failed = impl_->failed.load(std::memory_order_relaxed);
  stats.rejected_full =
      impl_->rejected_full.load(std::memory_order_relaxed);
  stats.rejected_contention =
      impl_->rejected_contention.load(std::memory_order_relaxed);
  stats.rejected_closed =
      impl_->rejected_closed.load(std::memory_order_relaxed);
  stats.dropped_lower_priority =
      impl_->dropped_lower_priority.load(std::memory_order_relaxed);
  std::scoped_lock lock{impl_->queue_mutex};
  stats.queued = impl_->queue.size();
  return stats;
}

std::string AsyncStorageWriter::LastError() const {
  if (impl_ == nullptr) {
    return {};
  }
  std::scoped_lock lock{impl_->error_mutex};
  return impl_->last_error;
}

}  // namespace lol_assistant::storage
