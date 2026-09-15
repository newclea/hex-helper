#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/detector/augment_screen_detector.h"
#include "lol_assistant/state/phase1_game_state_reducer.h"
#include "lol_assistant/vision/recognition_pipeline.h"
#include "session_runtime.h"

namespace lol_assistant::app {

struct FrameProcessOptions final {
  bool force_recognition{false};
  bool reread_offer{false};
};

struct FrameProcessResult final {
  detector::DetectorResult raw_detector{};
  detector::DetectorResult stable_detector{};
  bool ocr_executed{false};
  bool accepted{false};
  bool duplicate{false};
  bool force_recognition{false};
  bool detector_stability_bypassed{false};
  std::uint32_t recommended_processing_hz{4U};
  std::string reason{"not_processed"};
  std::optional<std::string> offer_json{};
  std::optional<detector::ThreeCardRois> rois{};
  std::optional<vision::OfferRecognitionOutput> recognition{};
};

// Narrow persistence seam used by processor fault-injection tests. Product
// construction continues to bind directly to Phase1SessionRuntime.
class IOfferSessionRuntime {
 public:
  virtual ~IOfferSessionRuntime() = default;

  [[nodiscard]] virtual bool IsOpen() const noexcept = 0;
  [[nodiscard]] virtual AcceptedOfferResult AcceptOffer(
      const common::GameState& state,
      const std::array<vision::CardRecognitionOutput,
                       common::kAugmentCardCount>& card_outputs,
      const common::Frame& raw_frame, const detector::ThreeCardRois& rois,
      std::optional<std::string> selected_augment_id) = 0;
};

class AugmentFrameProcessor final {
 public:
  AugmentFrameProcessor(
      const vision::IOcrTitleRecognizer& ocr,
      std::vector<vision::TitleCandidate> mode_candidates,
      Phase1SessionRuntime& session_runtime,
      std::optional<std::string> manual_champion = std::nullopt,
      std::optional<std::size_t> selected_slot = std::nullopt,
      detector::AugmentScreenDetectorConfig detector_config = {},
      detector::StableDetectorConfig confirmer_config = {},
      float minimum_stable_confidence = 0.85F,
      std::vector<vision::IconHashTemplate> icon_templates = {},
      std::optional<std::string> icon_mode = std::nullopt,
      std::uint32_t completed_offers = 0U,
      bool recognize_icons = true);

  AugmentFrameProcessor(
      const vision::IOcrTitleRecognizer& ocr,
      std::vector<vision::TitleCandidate> mode_candidates,
      IOfferSessionRuntime& session_runtime,
      std::optional<std::string> manual_champion = std::nullopt,
      std::optional<std::size_t> selected_slot = std::nullopt,
      detector::AugmentScreenDetectorConfig detector_config = {},
      detector::StableDetectorConfig confirmer_config = {},
      float minimum_stable_confidence = 0.85F,
      std::vector<vision::IconHashTemplate> icon_templates = {},
      std::optional<std::string> icon_mode = std::nullopt,
      std::uint32_t completed_offers = 0U,
      bool recognize_icons = true);

  AugmentFrameProcessor(const AugmentFrameProcessor&) = delete;
  AugmentFrameProcessor& operator=(const AugmentFrameProcessor&) = delete;

  [[nodiscard]] FrameProcessResult Process(const common::Frame& frame);
  [[nodiscard]] FrameProcessResult Process(
      const common::Frame& frame, const FrameProcessOptions& options);
  void RequestImmediateReread() noexcept;

 private:
  struct TitleContentSignature final {
    std::array<std::uint64_t, common::kAugmentCardCount> hashes{};
    std::array<std::uint8_t, common::kAugmentCardCount> mean_luma{};
  };

  struct OcrConsensus final {
    std::array<std::string, common::kAugmentCardCount> ids{};
    std::array<bool, common::kAugmentCardCount> strong_evidence{};
    vision::OfferRecognitionOutput recognition{};
    std::uint32_t consecutive_frames{0U};
  };

  void Initialize(std::optional<std::string> manual_champion);
  void ResetContentAttempt() noexcept;
  void RegisterRetryableFailure(FrameProcessResult& result, std::string reason);
  [[nodiscard]] bool RuntimeIsOpen() const noexcept;
  [[nodiscard]] AcceptedOfferResult PersistOffer(
      const common::GameState& state,
      const std::array<vision::CardRecognitionOutput,
                       common::kAugmentCardCount>& card_outputs,
      const common::Frame& raw_frame, const detector::ThreeCardRois& rois,
      std::optional<std::string> selected_augment_id);
  [[nodiscard]] FrameProcessResult PersistConsensus(
      FrameProcessResult result, const common::Frame& frame,
      const detector::DetectorResult& recognition_detector,
      bool force_recognition);
  [[nodiscard]] static std::optional<TitleContentSignature>
  ComputeTitleContentSignature(const common::Frame& frame,
                               const detector::ThreeCardRois& rois) noexcept;
  [[nodiscard]] static bool IsSignificantContentChange(
      const TitleContentSignature& left,
      const TitleContentSignature& right) noexcept;
  void ApplyIconRecognition(const common::Frame& frame,
                            const detector::ThreeCardRois& rois,
                            vision::OfferRecognitionOutput& recognition) const;

  detector::AugmentScreenDetector detector_;
  detector::ConsecutiveFrameConfirmer confirmer_;
  bool ocr_unconfirmed_rois_{false};
  std::unique_ptr<vision::IOcrTitleRecognizer> preprocessing_ocr_{};
  vision::AugmentRecognitionPipeline recognition_pipeline_;
  vision::PerceptualHashTemplateMatcher icon_matcher_;
  std::optional<std::string> icon_mode_{};
  float minimum_stable_confidence_{0.85F};
  std::uint32_t completed_offers_{0U};
  state::Phase1GameStateReducer reducer_;
  Phase1SessionRuntime* session_runtime_{nullptr};
  IOfferSessionRuntime* injected_session_runtime_{nullptr};
  std::optional<std::size_t> selected_slot_{};
  bool recognize_icons_{true};
  std::optional<TitleContentSignature> active_content_signature_{};
  std::optional<TitleContentSignature> pending_content_signature_{};
  std::uint32_t pending_content_confirmation_frames_{0U};
  std::optional<OcrConsensus> ocr_consensus_{};
  std::optional<vision::OfferRecognitionOutput> persistable_consensus_{};
  std::optional<vision::OfferRecognitionOutput> last_recognition_{};
  std::uint32_t retryable_failures_{0U};
  std::uint32_t retry_backoff_frames_{0U};
  bool content_processed_{false};
  bool content_attempt_exhausted_{false};
  std::uint32_t consecutive_invisible_frames_{0U};
  bool dismissal_handled_{false};
};

}  // namespace lol_assistant::app
