#include "session_runtime.h"

#include <Windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <unordered_set>
#include <utility>

#include "../output/json_support.h"
#include "lol_assistant/output/structured_json_writer.h"
#include "lol_assistant/storage/jsonl_writer.h"
#include "lol_assistant/storage/session_store.h"

namespace lol_assistant::app {
namespace {

using storage::StorageErrorCode;
using storage::StorageStatus;

[[nodiscard]] bool IsValidUtf8(const std::string_view value) noexcept {
  std::size_t index = 0U;
  while (index < value.size()) {
    const auto first = static_cast<std::uint8_t>(value[index]);
    if (first <= 0x7FU) {
      ++index;
      continue;
    }
    std::size_t continuation_count = 0U;
    std::uint32_t code_point = 0U;
    std::uint32_t minimum = 0U;
    if (first >= 0xC2U && first <= 0xDFU) {
      continuation_count = 1U;
      code_point = first & 0x1FU;
      minimum = 0x80U;
    } else if (first >= 0xE0U && first <= 0xEFU) {
      continuation_count = 2U;
      code_point = first & 0x0FU;
      minimum = 0x800U;
    } else if (first >= 0xF0U && first <= 0xF4U) {
      continuation_count = 3U;
      code_point = first & 0x07U;
      minimum = 0x10000U;
    } else {
      return false;
    }
    if (index + continuation_count >= value.size()) {
      return false;
    }
    for (std::size_t offset = 1U; offset <= continuation_count; ++offset) {
      const auto continuation =
          static_cast<std::uint8_t>(value[index + offset]);
      if ((continuation & 0xC0U) != 0x80U) {
        return false;
      }
      code_point = (code_point << 6U) | (continuation & 0x3FU);
    }
    if (code_point < minimum || code_point > 0x10FFFFU ||
        (code_point >= 0xD800U && code_point <= 0xDFFFU)) {
      return false;
    }
    index += continuation_count + 1U;
  }
  return true;
}

[[nodiscard]] bool IsNonEmptyUtf8(const std::string_view value) noexcept {
  return !value.empty() && IsValidUtf8(value);
}

[[nodiscard]] bool IsOptionalNonEmptyUtf8(
    const std::optional<std::string>& value) noexcept {
  return !value.has_value() || IsNonEmptyUtf8(*value);
}

[[nodiscard]] bool HasDotDotComponent(
    const std::filesystem::path& path) noexcept {
  return std::any_of(path.begin(), path.end(), [](const auto& component) {
    return component == L"..";
  });
}

[[nodiscard]] bool IsStrictDescendant(const std::filesystem::path& root,
                                      const std::filesystem::path& child) {
  const auto normalized_root = std::filesystem::weakly_canonical(root);
  const auto normalized_child = std::filesystem::weakly_canonical(child);
  auto root_part = normalized_root.begin();
  auto child_part = normalized_child.begin();
  for (; root_part != normalized_root.end(); ++root_part, ++child_part) {
    if (child_part == normalized_child.end() || *root_part != *child_part) {
      return false;
    }
  }
  return child_part != normalized_child.end();
}

[[nodiscard]] SessionRuntimeStatus FromStorageStatus(
    const StorageStatus& status, const std::string_view context) {
  SessionRuntimeError code = SessionRuntimeError::StorageError;
  if (status.code == StorageErrorCode::InvalidPath) {
    code = SessionRuntimeError::InvalidPath;
  } else if (status.code == StorageErrorCode::InvalidArgument ||
             status.code == StorageErrorCode::InvalidUtf8) {
    code = SessionRuntimeError::InvalidArgument;
  } else if (status.code == StorageErrorCode::Closed) {
    code = SessionRuntimeError::Closed;
  }
  std::string message{context};
  message.append(": ");
  message.append(status.message);
  if (status.native_code != 0) {
    message.append(" (native=");
    message.append(std::to_string(status.native_code));
    message.push_back(')');
  }
  return {code, std::move(message)};
}

[[nodiscard]] std::string GenerateSessionId() {
  std::array<unsigned char, 16U> random_bytes{};
  const NTSTATUS status = BCryptGenRandom(
      nullptr, random_bytes.data(), static_cast<ULONG>(random_bytes.size()),
      BCRYPT_USE_SYSTEM_PREFERRED_RNG);
  if (status < 0) {
    throw std::runtime_error("BCryptGenRandom failed while creating session_id");
  }
  constexpr char hex[] = "0123456789abcdef";
  std::string id{"phase1-"};
  id.reserve(7U + random_bytes.size() * 2U);
  for (const unsigned char value : random_bytes) {
    id.push_back(hex[(value >> 4U) & 0x0FU]);
    id.push_back(hex[value & 0x0FU]);
  }
  return id;
}

void AppendOptionalString(std::string& json,
                          const std::optional<std::string>& value) {
  if (value.has_value()) {
    output::detail::AppendQuotedUtf8(json, *value);
  } else {
    json.append("null");
  }
}

void AppendOptionalFloat(std::string& json,
                         const std::optional<float>& value) {
  if (value.has_value()) {
    output::detail::AppendFloating(json, *value);
  } else {
    json.append("null");
  }
}

[[nodiscard]] std::string SessionMetadataJson(
    const std::string_view mode, const SessionSourceMetadata& source) {
  std::string json;
  json.reserve(256U);
  json.append("{\"mode\":");
  output::detail::AppendQuotedUtf8(json, mode);
  json.append(",\"source\":{");
  json.append("\"name\":");
  output::detail::AppendQuotedUtf8(json, source.source);
  json.append(",\"backend\":");
  output::detail::AppendQuotedUtf8(json, source.backend);
  json.append(",\"catalog_version\":");
  output::detail::AppendQuotedUtf8(json, source.catalog_version);
  json.append(",\"source_id\":");
  AppendOptionalString(json, source.source_id);
  json.append("}}");
  return json;
}

[[nodiscard]] std::string CardStateName(
    const vision::CardRecognitionState state) {
  switch (state) {
    case vision::CardRecognitionState::SkippedDetectorUnstable:
      return "SKIPPED_DETECTOR_UNSTABLE";
    case vision::CardRecognitionState::BackendUnavailable:
      return "BACKEND_UNAVAILABLE";
    case vision::CardRecognitionState::OcrFailed:
      return "OCR_FAILED";
    case vision::CardRecognitionState::Unknown:
      return "UNKNOWN";
    case vision::CardRecognitionState::Recognized:
      return "RECOGNIZED";
  }
  return "UNKNOWN";
}

[[nodiscard]] std::string IconStateName(const vision::IconMatchState state) {
  switch (state) {
    case vision::IconMatchState::Unavailable:
      return "UNAVAILABLE";
    case vision::IconMatchState::Unknown:
      return "UNKNOWN";
    case vision::IconMatchState::Matched:
      return "MATCHED";
  }
  return "UNKNOWN";
}

[[nodiscard]] std::string CardRawJson(
    const vision::CardRecognitionOutput& card) {
  std::string json;
  json.reserve(512U);
  json.append("{\"state\":");
  output::detail::AppendQuotedUtf8(json, CardStateName(card.state));
  json.append(",\"raw_text\":");
  output::detail::AppendQuotedUtf8(json, card.raw_text);
  json.append(",\"backend\":");
  output::detail::AppendQuotedUtf8(json, card.backend);
  json.append(",\"ocr_confidence\":");
  AppendOptionalFloat(json, card.ocr_confidence);
  json.append(",\"match_confidence\":");
  output::detail::AppendFloating(json, card.match_confidence);
  json.append(",\"final_confidence\":");
  output::detail::AppendFloating(json, card.final_confidence);
  json.append(",\"augment_id\":");
  AppendOptionalString(json, card.augment_id);
  json.append(",\"display_name\":");
  AppendOptionalString(json, card.display_name);
  json.append(",\"reason\":");
  output::detail::AppendQuotedUtf8(json, card.reason);
  json.append(",\"icon_match\":{\"state\":");
  output::detail::AppendQuotedUtf8(json, IconStateName(card.icon_match.state));
  json.append(",\"augment_id\":");
  AppendOptionalString(json, card.icon_match.augment_id);
  json.append(",\"confidence\":");
  AppendOptionalFloat(json, card.icon_match.confidence);
  json.append(",\"reason\":");
  output::detail::AppendQuotedUtf8(json, card.icon_match.reason);
  json.append("}}");
  return json;
}

[[nodiscard]] std::string OfferFingerprint(
    const std::uint32_t round,
    const std::array<std::string, common::kAugmentCardCount>& ids) {
  constexpr std::uint64_t offset_basis = 14695981039346656037ULL;
  constexpr std::uint64_t prime = 1099511628211ULL;
  std::uint64_t hash = offset_basis;
  const auto append = [&hash](const std::string_view value) {
    for (const unsigned char byte : value) {
      hash ^= byte;
      hash *= prime;
    }
    hash ^= 0xFFU;
    hash *= prime;
  };
  append(std::to_string(round));
  for (const auto& id : ids) {
    append(id);
  }
  constexpr char hex[] = "0123456789abcdef";
  std::string fingerprint(16U, '0');
  for (std::size_t index = 0U; index < fingerprint.size(); ++index) {
    const std::size_t shift = (fingerprint.size() - 1U - index) * 4U;
    fingerprint[index] = hex[(hash >> shift) & 0x0FU];
  }
  return fingerprint;
}

[[nodiscard]] common::CardSlot PixelSlot(
    const common::CardSlotId id, const detector::PixelRoi& roi,
    const common::Frame& frame) {
  const double width = static_cast<double>(frame.width);
  const double height = static_cast<double>(frame.height);
  // DebugArtifactWriter converts normalized bounds with floor(left/top) and
  // ceil(right/bottom). Keep each floating bound one quarter pixel inside the
  // caller's integer ROI so binary rounding cannot grow a crop by one pixel.
  constexpr double inset = 0.25;
  const double left = (static_cast<double>(roi.x) + inset) / width;
  const double top = (static_cast<double>(roi.y) + inset) / height;
  const double right =
      (static_cast<double>(roi.x + roi.width) - inset) / width;
  const double bottom =
      (static_cast<double>(roi.y + roi.height) - inset) / height;
  return {id,
          {left, top, right - left, bottom - top}};
}

void RemoveArtifacts(const output::DebugArtifactSet& artifacts) noexcept {
  const std::array<std::filesystem::path, 5U> paths{
      artifacts.raw_frame, artifacts.card_rois[0U], artifacts.card_rois[1U],
      artifacts.card_rois[2U], artifacts.sidecar_json};
  for (const auto& path : paths) {
    std::error_code ignored;
    (void)std::filesystem::remove(path, ignored);
  }
}

[[nodiscard]] SessionRuntimeStatus ValidateCreateArguments(
    const std::filesystem::path& runtime_root,
    const std::optional<std::string>& champion, const std::string_view mode,
    const SessionSourceMetadata& source) {
  if (runtime_root.empty() || !runtime_root.is_absolute() ||
      HasDotDotComponent(runtime_root)) {
    return {SessionRuntimeError::InvalidPath,
            "runtime_root must be an explicit absolute path without '..'"};
  }
  if (!IsOptionalNonEmptyUtf8(champion) || !IsNonEmptyUtf8(mode) ||
      !IsNonEmptyUtf8(source.source) || !IsNonEmptyUtf8(source.backend) ||
      !IsNonEmptyUtf8(source.catalog_version) ||
      !IsOptionalNonEmptyUtf8(source.source_id)) {
    return {SessionRuntimeError::InvalidArgument,
            "champion, mode, and source metadata must be valid UTF-8; "
            "present values must not be empty"};
  }
  return SessionRuntimeStatus::Ok();
}

}  // namespace

struct Phase1SessionRuntime::Impl final {
  std::filesystem::path runtime_root{};
  std::filesystem::path output_directory{};
  std::filesystem::path database_path{};
  std::filesystem::path jsonl_path{};
  std::string session_id{};
  std::optional<std::string> champion{};
  std::string mode{};
  SessionSourceMetadata source{};
  std::unique_ptr<storage::SessionStore> store{};
  std::unique_ptr<storage::JsonlWriter> jsonl_writer{};
  std::unique_ptr<output::DebugArtifactWriter> artifact_writer{};
  output::DefaultDebugArtifactPolicy artifact_policy{0.5F, true, true};
  std::unordered_set<std::string> fingerprints{};
  std::array<bool, 5U> accepted_rounds{};
  std::int64_t next_offer_id{1};
  bool closed{false};
  mutable std::mutex mutex{};

  [[nodiscard]] SessionRuntimeStatus RestoreJsonl(
      const std::uintmax_t original_size) {
    jsonl_writer.reset();
    std::error_code error;
    std::filesystem::resize_file(jsonl_path, original_size, error);
    if (error) {
      return {SessionRuntimeError::StorageError,
              "failed to restore JSONL after transaction rollback: " +
                  error.message()};
    }
    const StorageStatus reopen = storage::JsonlWriter::Open(
        jsonl_path, runtime_root, jsonl_writer);
    if (!reopen.IsSuccess()) {
      return FromStorageStatus(reopen,
                               "failed to reopen JSONL after rollback");
    }
    return SessionRuntimeStatus::Ok();
  }
};

Phase1SessionRuntime::Phase1SessionRuntime(
    std::unique_ptr<Impl> impl) noexcept
    : impl_(std::move(impl)) {}

Phase1SessionRuntime::Phase1SessionRuntime(
    Phase1SessionRuntime&& other) noexcept = default;

Phase1SessionRuntime& Phase1SessionRuntime::operator=(
    Phase1SessionRuntime&& other) noexcept {
  if (this != &other) {
    (void)Close();
    impl_ = std::move(other.impl_);
  }
  return *this;
}

Phase1SessionRuntime::~Phase1SessionRuntime() { (void)Close(); }

SessionRuntimeStatus Phase1SessionRuntime::Create(
    const std::filesystem::path& runtime_root,
    std::optional<std::string> champion, std::string mode,
    SessionSourceMetadata source_metadata,
    std::unique_ptr<Phase1SessionRuntime>& runtime) {
  runtime.reset();
  const auto validation = ValidateCreateArguments(
      runtime_root, champion, mode, source_metadata);
  if (!validation.IsSuccess()) {
    return validation;
  }

  std::filesystem::path session_directory;
  try {
    std::error_code error;
    std::filesystem::create_directories(runtime_root, error);
    if (error || !std::filesystem::is_directory(runtime_root)) {
      return {SessionRuntimeError::InvalidPath,
              "runtime_root could not be created as a directory: " +
                  error.message()};
    }
    const auto canonical_root = std::filesystem::weakly_canonical(runtime_root);

    std::string session_id;
    for (std::size_t attempt = 0U; attempt < 16U; ++attempt) {
      session_id = GenerateSessionId();
      session_directory = canonical_root / session_id;
      if (std::filesystem::create_directory(session_directory, error)) {
        break;
      }
      if (error) {
        return {SessionRuntimeError::InvalidPath,
                "session output directory could not be created: " +
                    error.message()};
      }
      session_directory.clear();
    }
    if (session_directory.empty() ||
        !IsStrictDescendant(canonical_root, session_directory)) {
      return {SessionRuntimeError::InvalidPath,
              "failed to reserve a safe session directory under runtime_root"};
    }

    auto implementation = std::make_unique<Impl>();
    implementation->runtime_root = canonical_root;
    implementation->output_directory =
        std::filesystem::weakly_canonical(session_directory);
    implementation->session_id = session_id;
    implementation->database_path =
        implementation->output_directory / L"session.sqlite3";
    implementation->jsonl_path =
        implementation->output_directory / L"events.jsonl";
    implementation->champion = std::move(champion);
    implementation->mode = std::move(mode);
    implementation->source = std::move(source_metadata);

    StorageStatus status = storage::SessionStore::Open(
        implementation->database_path, implementation->runtime_root,
        implementation->store);
    if (!status.IsSuccess()) {
      const auto mapped = FromStorageStatus(status, "SessionStore::Open");
      implementation.reset();
      std::filesystem::remove_all(session_directory, error);
      return mapped;
    }
    status = storage::JsonlWriter::Open(
        implementation->jsonl_path, implementation->runtime_root,
        implementation->jsonl_writer);
    if (!status.IsSuccess()) {
      const auto mapped = FromStorageStatus(status, "JsonlWriter::Open");
      implementation.reset();
      std::filesystem::remove_all(session_directory, error);
      return mapped;
    }
    implementation->artifact_writer =
        std::make_unique<output::DebugArtifactWriter>(canonical_root);

    const auto started_at = common::UtcTimestamp::clock::now();
    const std::string timestamp =
        output::detail::FormatUtcTimestamp(started_at);
    const storage::SessionRecord session_record{
        implementation->session_id, timestamp, implementation->champion,
        SessionMetadataJson(implementation->mode, implementation->source)};
    storage::JsonlEvent start_event;
    start_event.event_type = "start";
    start_event.timestamp_utc = timestamp;
    start_event.session_id = implementation->session_id;
    start_event.fields.emplace("mode", implementation->mode);
    start_event.fields.emplace("champion", implementation->champion.has_value()
                                               ? storage::JsonScalar{
                                                     *implementation->champion}
                                               : storage::JsonScalar{nullptr});

    storage::JsonlEvent diagnostic_event;
    diagnostic_event.event_type = "diagnostic";
    diagnostic_event.timestamp_utc = timestamp;
    diagnostic_event.session_id = implementation->session_id;
    diagnostic_event.fields.emplace("status", "runtime_ready");
    diagnostic_event.fields.emplace("source", implementation->source.source);
    diagnostic_event.fields.emplace("backend",
                                    implementation->source.backend);
    diagnostic_event.fields.emplace("catalog_version",
                                    implementation->source.catalog_version);

    status = implementation->store->RunInTransaction(
        [&](storage::SessionStore& transaction_store) {
          auto step = transaction_store.CreateSession(session_record);
          if (!step.IsSuccess()) {
            return step;
          }
          step = implementation->jsonl_writer->Write(start_event);
          if (!step.IsSuccess()) {
            return step;
          }
          step = implementation->jsonl_writer->Write(diagnostic_event);
          if (!step.IsSuccess()) {
            return step;
          }
          return implementation->jsonl_writer->Flush();
        });
    if (!status.IsSuccess()) {
      const auto mapped = FromStorageStatus(status, "create session transaction");
      implementation.reset();
      std::filesystem::remove_all(session_directory, error);
      return mapped;
    }

    runtime.reset(new Phase1SessionRuntime(std::move(implementation)));
    return SessionRuntimeStatus::Ok();
  } catch (const std::filesystem::filesystem_error& error) {
    std::error_code ignored;
    if (!session_directory.empty()) {
      std::filesystem::remove_all(session_directory, ignored);
    }
    return {SessionRuntimeError::InvalidPath,
            std::string{"filesystem error while creating session: "} +
                error.what()};
  } catch (const std::exception& error) {
    std::error_code ignored;
    if (!session_directory.empty()) {
      std::filesystem::remove_all(session_directory, ignored);
    }
    return {SessionRuntimeError::StorageError,
            std::string{"failed to create session runtime: "} + error.what()};
  } catch (...) {
    std::error_code ignored;
    if (!session_directory.empty()) {
      std::filesystem::remove_all(session_directory, ignored);
    }
    return {SessionRuntimeError::StorageError,
            "failed to create session runtime: unknown error"};
  }
}

AcceptedOfferResult Phase1SessionRuntime::AcceptOffer(
    const common::GameState& state,
    const std::array<vision::CardRecognitionOutput,
                     common::kAugmentCardCount>& card_outputs,
    const common::Frame& raw_frame, const detector::ThreeCardRois& rois,
    std::optional<std::string> selected_augment_id) {
  AcceptedOfferResult result;
  if (impl_ == nullptr) {
    result.status = {SessionRuntimeError::Closed,
                     "session runtime has been moved or closed"};
    return result;
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->closed || impl_->store == nullptr ||
      impl_->jsonl_writer == nullptr) {
    result.status = {SessionRuntimeError::Closed,
                     "session runtime is closed"};
    return result;
  }

  try {
    if (!state.IsValid() || !state.current_offer.has_value() ||
        !state.offer_round.has_value() || *state.offer_round < 1U ||
        *state.offer_round > 4U ||
        state.champion != impl_->champion ||
        !IsOptionalNonEmptyUtf8(state.champion)) {
      result.status = {
          SessionRuntimeError::InvalidArgument,
          "stable GameState is invalid, incomplete, out of Phase 1 range, or "
          "does not match the session champion"};
      return result;
    }
    const auto& offer = *state.current_offer;
    if (!offer.screen_detection.has_value() ||
        offer.screen_detection->state != common::DetectionState::Detected ||
        !offer.metadata.observed_at.has_value()) {
      result.status = {SessionRuntimeError::InvalidArgument,
                       "accepted offer requires a detected screen and an "
                       "offer observation timestamp"};
      return result;
    }
    if (!raw_frame.IsValid() ||
        !detector::IsSupported16By9(raw_frame.width, raw_frame.height)) {
      result.status = {
          SessionRuntimeError::InvalidArgument,
          "raw frame must be a valid supported 16:9 owning BGRA8 frame"};
      return result;
    }
    if (!rois.offer_region.IsInside(raw_frame.width, raw_frame.height) ||
        !std::all_of(rois.cards.begin(), rois.cards.end(),
                     [&](const detector::PixelRoi& roi) {
                       return roi.IsInside(raw_frame.width, raw_frame.height);
                     })) {
      result.status = {SessionRuntimeError::InvalidArgument,
                       "offer/card ROIs must be non-empty and inside the raw "
                       "frame"};
      return result;
    }

    std::array<const common::AugmentRecognition*,
               common::kAugmentCardCount>
        recognitions{};
    for (const auto& candidate : offer.recognitions) {
      if (!candidate.has_value() ||
          candidate->state != common::RecognitionState::Recognized) {
        result.status = {SessionRuntimeError::InvalidArgument,
                         "accepted offer must contain three recognized cards"};
        return result;
      }
      const auto slot = static_cast<std::size_t>(candidate->slot);
      if (slot < 1U || slot > common::kAugmentCardCount ||
          recognitions[slot - 1U] != nullptr ||
          !IsOptionalNonEmptyUtf8(candidate->augment_id) ||
          !IsOptionalNonEmptyUtf8(candidate->display_name)) {
        result.status = {SessionRuntimeError::InvalidArgument,
                         "offer recognitions must have unique valid "
                         "LEFT/CENTER/RIGHT UTF-8 fields"};
        return result;
      }
      recognitions[slot - 1U] = &*candidate;
    }

    std::array<std::string, common::kAugmentCardCount> augment_ids{};
    for (std::size_t index = 0U; index < card_outputs.size(); ++index) {
      const auto& card = card_outputs[index];
      const auto* recognition = recognitions[index];
      const bool confidences_valid =
          std::isfinite(card.match_confidence) &&
          card.match_confidence >= 0.0F && card.match_confidence <= 1.0F &&
          std::isfinite(card.final_confidence) &&
          card.final_confidence >= 0.0F && card.final_confidence <= 1.0F &&
          (!card.ocr_confidence.has_value() ||
           (std::isfinite(*card.ocr_confidence) &&
            *card.ocr_confidence >= 0.0F &&
            *card.ocr_confidence <= 1.0F));
      if (recognition == nullptr ||
          card.state != vision::CardRecognitionState::Recognized ||
          !IsOptionalNonEmptyUtf8(card.augment_id) ||
          !card.augment_id.has_value() || !IsNonEmptyUtf8(card.backend) ||
          !confidences_valid || card.augment_id != recognition->augment_id ||
          (card.display_name.has_value() && recognition->display_name.has_value() &&
           card.display_name != recognition->display_name)) {
        result.status = {
            SessionRuntimeError::InvalidArgument,
            "caller-provided card output is not stable or does not map to the "
            "GameState recognition at the same semantic slot"};
        return result;
      }
      augment_ids[index] = *card.augment_id;
    }
    if (augment_ids[0U] == augment_ids[1U] ||
        augment_ids[0U] == augment_ids[2U] ||
        augment_ids[1U] == augment_ids[2U]) {
      result.status = {SessionRuntimeError::InvalidArgument,
                       "accepted offer augment IDs must be distinct"};
      return result;
    }
    if (selected_augment_id.has_value() &&
        (!IsNonEmptyUtf8(*selected_augment_id) ||
         std::find(augment_ids.begin(), augment_ids.end(),
                   *selected_augment_id) == augment_ids.end())) {
      result.status = {SessionRuntimeError::InvalidArgument,
                       "selected augment must be one of the three offered IDs"};
      return result;
    }

    const std::uint32_t offer_round = *state.offer_round;
    result.fingerprint = OfferFingerprint(offer_round, augment_ids);
    if (impl_->fingerprints.contains(result.fingerprint)) {
      result.status = {SessionRuntimeError::DuplicateOffer,
                       "offer fingerprint was already accepted; no rows or "
                       "artifacts were written"};
      return result;
    }
    if (impl_->accepted_rounds[offer_round]) {
      result.status = {SessionRuntimeError::InvalidArgument,
                       "a different offer was already accepted for this round"};
      return result;
    }

    result.stdout_json = output::StructuredJsonWriter::WriteGameState(state);
    const auto [fingerprint_it, inserted] =
        impl_->fingerprints.insert(result.fingerprint);
    if (!inserted) {
      result.status = {SessionRuntimeError::DuplicateOffer,
                       "offer fingerprint was already accepted"};
      return result;
    }

    const std::string observed_at = output::detail::FormatUtcTimestamp(
        *offer.metadata.observed_at);
    std::error_code size_error;
    const std::uintmax_t original_jsonl_size =
        std::filesystem::file_size(impl_->jsonl_path, size_error);
    if (size_error) {
      impl_->fingerprints.erase(fingerprint_it);
      result.status = {
          SessionRuntimeError::StorageError,
          "failed to establish JSONL rollback boundary: " +
              size_error.message()};
      return result;
    }

    output::DebugArtifactRequest artifact_request;
    artifact_request.session_id = impl_->session_id;
    artifact_request.timestamp = *offer.metadata.observed_at;
    artifact_request.confidence = offer.metadata.confidence;
    artifact_request.backend = impl_->source.backend;
    artifact_request.catalog_version = impl_->source.catalog_version;
    artifact_request.trigger = output::DebugArtifactTrigger::Manual;
    artifact_request.card_slots = {
        PixelSlot(common::CardSlotId::Left, rois.cards[0U], raw_frame),
        PixelSlot(common::CardSlotId::Center, rois.cards[1U], raw_frame),
        PixelSlot(common::CardSlotId::Right, rois.cards[2U], raw_frame)};
    result.artifacts = impl_->artifact_writer->WriteIfRequested(
        raw_frame, artifact_request, impl_->artifact_policy);
    if (!result.artifacts.has_value()) {
      impl_->fingerprints.erase(fingerprint_it);
      result.status = {SessionRuntimeError::ArtifactError,
                       "manual artifact policy unexpectedly declined offer"};
      return result;
    }

    const storage::AugmentOfferRecord offer_record{
        impl_->session_id, offer_round, observed_at,
        static_cast<double>(offer.metadata.confidence.value), augment_ids,
        result.stdout_json};
    std::array<storage::RecognitionResultRecord,
               common::kAugmentCardCount>
        recognition_records{};
    for (std::size_t index = 0U; index < recognition_records.size(); ++index) {
      const auto* recognition = recognitions[index];
      const auto recognition_time =
          recognition->metadata.observed_at.value_or(*offer.metadata.observed_at);
      recognition_records[index] = storage::RecognitionResultRecord{
          impl_->session_id,
          impl_->next_offer_id,
          static_cast<std::uint32_t>(index + 1U),
          static_cast<std::uint32_t>(common::RecognitionState::Recognized),
          recognition->augment_id,
          recognition->display_name,
          static_cast<double>(card_outputs[index].final_confidence),
          output::detail::FormatUtcTimestamp(recognition_time),
          CardRawJson(card_outputs[index])};
    }

    const std::array<std::string_view, 5U> artifact_kinds{
        "RAW", "LEFT", "CENTER", "RIGHT", "sidecar"};
    const std::array<std::filesystem::path, 5U> artifact_paths{
        result.artifacts->raw_frame, result.artifacts->card_rois[0U],
        result.artifacts->card_rois[1U], result.artifacts->card_rois[2U],
        result.artifacts->sidecar_json};

    storage::JsonlEvent accepted_event;
    accepted_event.event_type = "accepted_offer";
    accepted_event.timestamp_utc = observed_at;
    accepted_event.session_id = impl_->session_id;
    accepted_event.fields.emplace("offer_round",
                                  static_cast<std::int64_t>(offer_round));
    accepted_event.fields.emplace("fingerprint", result.fingerprint);
    accepted_event.fields.emplace("offer_json", result.stdout_json);
    accepted_event.fields.emplace(
        "selected_augment_id",
        selected_augment_id.has_value()
            ? storage::JsonScalar{*selected_augment_id}
            : storage::JsonScalar{nullptr});
    accepted_event.fields.emplace("artifact_count", std::int64_t{5});

    const StorageStatus transaction_status = impl_->store->RunInTransaction(
        [&](storage::SessionStore& transaction_store) {
          auto step = transaction_store.InsertAugmentOffer(offer_record);
          if (!step.IsSuccess()) {
            return step;
          }
          for (const auto& recognition : recognition_records) {
            step = transaction_store.InsertRecognitionResult(recognition);
            if (!step.IsSuccess()) {
              return step;
            }
          }
          if (selected_augment_id.has_value()) {
            step = transaction_store.InsertAugmentChoice(
                {impl_->session_id, offer_round, selected_augment_id, true,
                 observed_at});
            if (!step.IsSuccess()) {
              return step;
            }
          }
          for (std::size_t index = 0U; index < artifact_paths.size(); ++index) {
            step = transaction_store.InsertArtifact(
                {impl_->session_id, std::string{artifact_kinds[index]},
                 artifact_paths[index], std::nullopt, observed_at});
            if (!step.IsSuccess()) {
              return step;
            }
          }
          step = impl_->jsonl_writer->Write(accepted_event);
          if (!step.IsSuccess()) {
            return step;
          }
          return impl_->jsonl_writer->Flush();
        });
    if (!transaction_status.IsSuccess()) {
      RemoveArtifacts(*result.artifacts);
      result.artifacts.reset();
      impl_->fingerprints.erase(fingerprint_it);
      const auto restore = impl_->RestoreJsonl(original_jsonl_size);
      result.status = FromStorageStatus(transaction_status,
                                        "accept offer transaction");
      if (!restore.IsSuccess()) {
        result.status.message.append("; ");
        result.status.message.append(restore.message);
      }
      return result;
    }

    impl_->accepted_rounds[offer_round] = true;
    ++impl_->next_offer_id;
    result.accepted = true;
    result.status = SessionRuntimeStatus::Ok();
    return result;
  } catch (const std::invalid_argument& error) {
    if (result.artifacts.has_value()) {
      RemoveArtifacts(*result.artifacts);
      result.artifacts.reset();
    }
    if (!result.fingerprint.empty()) {
      impl_->fingerprints.erase(result.fingerprint);
    }
    result.status = {SessionRuntimeError::SerializationError,
                     std::string{"offer serialization failed: "} +
                         error.what()};
    return result;
  } catch (const std::exception& error) {
    if (result.artifacts.has_value()) {
      RemoveArtifacts(*result.artifacts);
      result.artifacts.reset();
    }
    if (!result.fingerprint.empty()) {
      impl_->fingerprints.erase(result.fingerprint);
    }
    result.status = {SessionRuntimeError::ArtifactError,
                     std::string{"offer persistence failed before commit: "} +
                         error.what()};
    return result;
  } catch (...) {
    if (result.artifacts.has_value()) {
      RemoveArtifacts(*result.artifacts);
      result.artifacts.reset();
    }
    if (!result.fingerprint.empty()) {
      impl_->fingerprints.erase(result.fingerprint);
    }
    result.status = {SessionRuntimeError::ArtifactError,
                     "offer persistence failed before commit: unknown error"};
    return result;
  }
}

SessionRuntimeStatus Phase1SessionRuntime::Close() {
  if (impl_ == nullptr) {
    return SessionRuntimeStatus::Ok();
  }
  std::scoped_lock lock{impl_->mutex};
  if (impl_->closed) {
    return SessionRuntimeStatus::Ok();
  }
  try {
    std::error_code size_error;
    const std::uintmax_t original_jsonl_size =
        std::filesystem::file_size(impl_->jsonl_path, size_error);
    if (size_error) {
      return {SessionRuntimeError::StorageError,
              "failed to establish JSONL close rollback boundary: " +
                  size_error.message()};
    }
    const std::string ended_at = output::detail::FormatUtcTimestamp(
        common::UtcTimestamp::clock::now());
    storage::JsonlEvent end_event;
    end_event.event_type = "end";
    end_event.timestamp_utc = ended_at;
    end_event.session_id = impl_->session_id;
    end_event.fields.emplace("status", "closed");

    const StorageStatus transaction_status = impl_->store->RunInTransaction(
        [&](storage::SessionStore& transaction_store) {
          auto step = transaction_store.EndSession(
              {impl_->session_id, ended_at});
          if (!step.IsSuccess()) {
            return step;
          }
          step = impl_->jsonl_writer->Write(end_event);
          if (!step.IsSuccess()) {
            return step;
          }
          return impl_->jsonl_writer->Flush();
        });
    if (!transaction_status.IsSuccess()) {
      const auto restore = impl_->RestoreJsonl(original_jsonl_size);
      auto mapped = FromStorageStatus(transaction_status,
                                      "close session transaction");
      if (!restore.IsSuccess()) {
        mapped.message.append("; ");
        mapped.message.append(restore.message);
      }
      return mapped;
    }

    const StorageStatus jsonl_close = impl_->jsonl_writer->Close();
    const StorageStatus store_close = impl_->store->Close();
    impl_->closed = true;
    if (!jsonl_close.IsSuccess()) {
      return FromStorageStatus(jsonl_close, "close JSONL writer");
    }
    if (!store_close.IsSuccess()) {
      return FromStorageStatus(store_close, "close SessionStore");
    }
    return SessionRuntimeStatus::Ok();
  } catch (const std::exception& error) {
    return {SessionRuntimeError::StorageError,
            std::string{"failed to close session runtime: "} + error.what()};
  } catch (...) {
    return {SessionRuntimeError::StorageError,
            "failed to close session runtime: unknown error"};
  }
}

bool Phase1SessionRuntime::IsOpen() const noexcept {
  return impl_ != nullptr && !impl_->closed;
}

const std::string& Phase1SessionRuntime::SessionId() const noexcept {
  static const std::string empty;
  return impl_ != nullptr ? impl_->session_id : empty;
}

const std::filesystem::path& Phase1SessionRuntime::OutputDirectory() const
    noexcept {
  static const std::filesystem::path empty;
  return impl_ != nullptr ? impl_->output_directory : empty;
}

const std::filesystem::path& Phase1SessionRuntime::DatabasePath() const
    noexcept {
  static const std::filesystem::path empty;
  return impl_ != nullptr ? impl_->database_path : empty;
}

const std::filesystem::path& Phase1SessionRuntime::JsonlPath() const noexcept {
  static const std::filesystem::path empty;
  return impl_ != nullptr ? impl_->jsonl_path : empty;
}

}  // namespace lol_assistant::app
