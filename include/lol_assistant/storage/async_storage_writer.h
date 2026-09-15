#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <variant>

#include "lol_assistant/storage/storage_types.h"

namespace lol_assistant::storage {

enum class StoragePriority : std::uint8_t {
  Low = 0,
  Normal = 1,
  Critical = 2,
};

enum class EnqueueResult : std::uint8_t {
  Accepted = 0,
  AcceptedAfterDroppingLowerPriority,
  RejectedFull,
  RejectedContention,
  RejectedClosed,
};

using StorageCommand =
    std::variant<SessionRecord, SessionEndRecord, AugmentOfferRecord,
                 AugmentChoiceRecord, RecognitionResultRecord, ArtifactRecord,
                 JsonlEvent>;

struct AsyncStorageOptions final {
  std::filesystem::path workspace_root{};
  std::filesystem::path database_path{};
  std::optional<std::filesystem::path> jsonl_path{};
  std::size_t maximum_queue_size{256U};
  std::chrono::milliseconds busy_timeout{2000};
};

struct AsyncStorageStats final {
  std::uint64_t accepted{0U};
  std::uint64_t processed{0U};
  std::uint64_t failed{0U};
  std::uint64_t rejected_full{0U};
  std::uint64_t rejected_contention{0U};
  std::uint64_t rejected_closed{0U};
  std::uint64_t dropped_lower_priority{0U};
  std::size_t queued{0U};
};

class AsyncStorageWriter final {
 public:
  ~AsyncStorageWriter();

  AsyncStorageWriter(const AsyncStorageWriter&) = delete;
  AsyncStorageWriter& operator=(const AsyncStorageWriter&) = delete;
  AsyncStorageWriter(AsyncStorageWriter&&) = delete;
  AsyncStorageWriter& operator=(AsyncStorageWriter&&) = delete;

  [[nodiscard]] static StorageStatus Start(
      const AsyncStorageOptions& options,
      std::unique_ptr<AsyncStorageWriter>& writer);

  // This method never performs disk I/O and never waits for queue ownership.
  [[nodiscard]] EnqueueResult TryEnqueue(
      StorageCommand command,
      StoragePriority priority = StoragePriority::Normal);
  // Shutdown is a control-thread operation: all accepted queued commands drain.
  [[nodiscard]] StorageStatus Shutdown();
  [[nodiscard]] AsyncStorageStats Stats() const noexcept;
  [[nodiscard]] std::string LastError() const;

 private:
  struct Impl;
  explicit AsyncStorageWriter(std::unique_ptr<Impl> implementation) noexcept;

  std::unique_ptr<Impl> impl_{};
};

}  // namespace lol_assistant::storage
