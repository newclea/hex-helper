#pragma once

#include <array>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/common/game_state.h"
#include "lol_assistant/detector/roi.h"
#include "lol_assistant/output/debug_artifact_writer.h"
#include "lol_assistant/vision/recognition_pipeline.h"

namespace lol_assistant::app {

enum class SessionRuntimeError {
  Ok = 0,
  DuplicateOffer,
  InvalidArgument,
  InvalidPath,
  StorageError,
  ArtifactError,
  SerializationError,
  Closed,
};

struct SessionRuntimeStatus final {
  SessionRuntimeError code{SessionRuntimeError::Ok};
  std::string message{};

  [[nodiscard]] bool IsSuccess() const noexcept {
    return code == SessionRuntimeError::Ok;
  }
  [[nodiscard]] bool IsDuplicate() const noexcept {
    return code == SessionRuntimeError::DuplicateOffer;
  }
  [[nodiscard]] static SessionRuntimeStatus Ok() { return {}; }
};

struct SessionSourceMetadata final {
  std::string source{};
  std::string backend{};
  std::string catalog_version{};
  std::optional<std::string> source_id{};
};

struct AcceptedOfferResult final {
  SessionRuntimeStatus status{};
  bool accepted{false};
  std::string fingerprint{};
  std::string stdout_json{};
  std::optional<output::DebugArtifactSet> artifacts{};
};

// Owns one Phase 1 session and its SQLite, JSONL, and debug artifacts.
// AcceptOffer consumes only stable caller-provided observations. It never runs
// detection, OCR, matching, frame capture, or input control.
class Phase1SessionRuntime final {
 public:
  [[nodiscard]] static SessionRuntimeStatus Create(
      const std::filesystem::path& runtime_root,
      std::optional<std::string> champion, std::string mode,
      SessionSourceMetadata source_metadata,
      std::unique_ptr<Phase1SessionRuntime>& runtime);

  Phase1SessionRuntime(Phase1SessionRuntime&&) noexcept;
  Phase1SessionRuntime& operator=(Phase1SessionRuntime&&) noexcept;
  ~Phase1SessionRuntime();

  Phase1SessionRuntime(const Phase1SessionRuntime&) = delete;
  Phase1SessionRuntime& operator=(const Phase1SessionRuntime&) = delete;

  [[nodiscard]] AcceptedOfferResult AcceptOffer(
      const common::GameState& state,
      const std::array<vision::CardRecognitionOutput,
                       common::kAugmentCardCount>& card_outputs,
      const common::Frame& raw_frame, const detector::ThreeCardRois& rois,
      std::optional<std::string> selected_augment_id = std::nullopt);

  [[nodiscard]] SessionRuntimeStatus Close();
  [[nodiscard]] bool IsOpen() const noexcept;
  [[nodiscard]] const std::string& SessionId() const noexcept;
  [[nodiscard]] const std::filesystem::path& OutputDirectory() const noexcept;
  [[nodiscard]] const std::filesystem::path& DatabasePath() const noexcept;
  [[nodiscard]] const std::filesystem::path& JsonlPath() const noexcept;

 private:
  struct Impl;
  explicit Phase1SessionRuntime(std::unique_ptr<Impl> impl) noexcept;

  std::unique_ptr<Impl> impl_;
};

}  // namespace lol_assistant::app
