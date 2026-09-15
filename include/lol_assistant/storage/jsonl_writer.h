#pragma once

#include <filesystem>
#include <memory>

#include "lol_assistant/storage/storage_types.h"

namespace lol_assistant::storage {

class JsonlWriter final {
 public:
  ~JsonlWriter();

  JsonlWriter(const JsonlWriter&) = delete;
  JsonlWriter& operator=(const JsonlWriter&) = delete;
  JsonlWriter(JsonlWriter&&) noexcept;
  JsonlWriter& operator=(JsonlWriter&&) noexcept;

  [[nodiscard]] static StorageStatus Open(
      const std::filesystem::path& jsonl_path,
      const std::filesystem::path& workspace_root,
      std::unique_ptr<JsonlWriter>& writer);

  [[nodiscard]] StorageStatus Write(const JsonlEvent& event);
  [[nodiscard]] StorageStatus Flush();
  [[nodiscard]] StorageStatus Close();
  [[nodiscard]] bool IsOpen() const noexcept;
  [[nodiscard]] const std::filesystem::path& Path() const noexcept;

 private:
  struct Impl;
  explicit JsonlWriter(std::unique_ptr<Impl> implementation) noexcept;

  std::unique_ptr<Impl> impl_{};
};

}  // namespace lol_assistant::storage
