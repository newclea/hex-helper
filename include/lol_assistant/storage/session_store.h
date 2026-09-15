#pragma once

#include <chrono>
#include <filesystem>
#include <functional>
#include <memory>

#include "lol_assistant/storage/storage_types.h"

namespace lol_assistant::storage {

class SessionStore final {
 public:
  using TransactionOperation = std::function<StorageStatus(SessionStore&)>;

  ~SessionStore();

  SessionStore(const SessionStore&) = delete;
  SessionStore& operator=(const SessionStore&) = delete;
  SessionStore(SessionStore&&) noexcept;
  SessionStore& operator=(SessionStore&&) noexcept;

  [[nodiscard]] static StorageStatus Open(
      const std::filesystem::path& database_path,
      const std::filesystem::path& workspace_root,
      std::unique_ptr<SessionStore>& store,
      std::chrono::milliseconds busy_timeout = std::chrono::milliseconds{2000});

  [[nodiscard]] StorageStatus CreateSession(const SessionRecord& record);
  [[nodiscard]] StorageStatus EndSession(const SessionEndRecord& record);
  [[nodiscard]] StorageStatus InsertAugmentOffer(
      const AugmentOfferRecord& record);
  [[nodiscard]] StorageStatus InsertAugmentChoice(
      const AugmentChoiceRecord& record);
  [[nodiscard]] StorageStatus InsertRecognitionResult(
      const RecognitionResultRecord& record);
  [[nodiscard]] StorageStatus InsertArtifact(const ArtifactRecord& record);

  [[nodiscard]] StorageStatus RunInTransaction(
      const TransactionOperation& operation);
  [[nodiscard]] StorageStatus Close();
  [[nodiscard]] bool IsOpen() const noexcept;
  [[nodiscard]] const std::filesystem::path& DatabasePath() const noexcept;
  [[nodiscard]] const std::filesystem::path& WorkspaceRoot() const noexcept;

 private:
  struct Impl;
  explicit SessionStore(std::unique_ptr<Impl> implementation) noexcept;

  std::unique_ptr<Impl> impl_{};
};

}  // namespace lol_assistant::storage
