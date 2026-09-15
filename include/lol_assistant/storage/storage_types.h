#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <variant>

namespace lol_assistant::storage {

enum class StorageErrorCode : std::uint8_t {
  Ok = 0,
  InvalidArgument,
  InvalidPath,
  OpenFailed,
  SqlError,
  SchemaTooNew,
  TransactionFailed,
  IoError,
  InvalidUtf8,
  Closed,
};

struct StorageStatus final {
  StorageErrorCode code{StorageErrorCode::Ok};
  int native_code{0};
  std::string message{};

  [[nodiscard]] bool IsSuccess() const noexcept {
    return code == StorageErrorCode::Ok;
  }

  [[nodiscard]] static StorageStatus Ok() { return {}; }
};

struct SessionRecord final {
  std::string session_id{};
  std::string started_at_utc{};
  std::optional<std::string> champion{};
  std::string metadata_json{"{}"};
};

struct SessionEndRecord final {
  std::string session_id{};
  std::string ended_at_utc{};
};

struct AugmentOfferRecord final {
  std::string session_id{};
  std::uint32_t offer_round{0U};
  std::string observed_at_utc{};
  double confidence{0.0};
  std::array<std::string, 3U> augment_ids{};
  std::string offer_json{"{}"};
};

struct AugmentChoiceRecord final {
  std::string session_id{};
  std::uint32_t offer_round{0U};
  std::optional<std::string> selected_augment_id{};
  bool confirmed{false};
  std::string observed_at_utc{};
};

struct RecognitionResultRecord final {
  std::string session_id{};
  std::optional<std::int64_t> offer_id{};
  std::uint32_t slot{0U};
  std::uint32_t recognition_state{0U};
  std::optional<std::string> augment_id{};
  std::optional<std::string> display_name{};
  double confidence{0.0};
  std::string observed_at_utc{};
  std::string raw_json{"{}"};
};

struct ArtifactRecord final {
  std::string session_id{};
  std::string artifact_kind{};
  std::filesystem::path artifact_path{};
  std::optional<std::string> sha256{};
  std::string created_at_utc{};
};

using JsonScalar =
    std::variant<std::nullptr_t, bool, std::int64_t, double, std::string>;

struct JsonlEvent final {
  std::string event_type{};
  std::string timestamp_utc{};
  std::optional<std::string> session_id{};
  std::map<std::string, JsonScalar, std::less<>> fields{};
};

}  // namespace lol_assistant::storage
