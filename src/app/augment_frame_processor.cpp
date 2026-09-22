#include "augment_frame_processor.h"

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "lol_assistant/common/augment_observation.h"
#include "lol_assistant/common/game_state.h"
#include "lol_assistant/common/geometry.h"
#include "lol_assistant/vision/title_preprocessor.h"

namespace lol_assistant::app {
namespace {

constexpr std::uint32_t kIdleProcessingHz = 4U;
constexpr std::uint32_t kActiveProcessingHz = 20U;
constexpr std::uint32_t kInvisibleFramesToRearm = 2U;
constexpr std::uint32_t kContentChangeConfirmationFrames = 2U;
constexpr std::uint32_t kOcrConsensusFrames = 2U;
constexpr std::uint32_t kMaximumRetryableFailures = 3U;
constexpr std::uint32_t kTitleHashColumns = 8U;
constexpr std::uint32_t kTitleHashRows = 8U;
constexpr std::uint32_t kSignificantHashBitChanges = 24U;
constexpr std::uint32_t kSignificantMeanLumaChange = 45U;
// OCR-only identity remains useful across a stable two-frame consensus, but
// it must rank below independently agreeing OCR and icon evidence.
constexpr float kOcrOnlyIconConfidenceScale = 0.90F;

class ProductTitlePreprocessingOcr final : public vision::IOcrTitleRecognizer {
 public:
  explicit ProductTitlePreprocessingOcr(
      const vision::IOcrTitleRecognizer& inner) noexcept
      : inner_(inner) {}

  [[nodiscard]] vision::OcrBackendStatus Probe() const noexcept override {
    return inner_.Probe();
  }

  [[nodiscard]] vision::OcrTextResult Recognize(
      const detector::OwningBgraCrop& title_crop) const noexcept override {
    try {
      if (title_crop.width >= 480U || title_crop.height >= 240U) {
        auto result = inner_.Recognize(title_crop);
        result.backend.append(";title_preprocess=skipped_full_frame");
        return result;
      }
      vision::TitlePreprocessParameters parameters;
      parameters.contrast_stretch = true;
      parameters.threshold_mode = vision::TitleThresholdMode::None;
      parameters.scale = title_crop.height > 0U && title_crop.height < 48U
                             ? 3U
                             : 2U;
      const auto processed =
          vision::TitlePreprocessor::Process(title_crop, parameters);
      if (!processed.ok()) {
        auto result = inner_.Recognize(title_crop);
        result.backend.append(";title_preprocess=raw_fallback;diagnostic=");
        result.backend.append(processed.reason);
        return result;
      }

      const auto& variant = *processed.value;
      detector::OwningBgraCrop ocr_crop;
      ocr_crop.width = variant.image.width;
      ocr_crop.height = variant.image.height;
      ocr_crop.stride = variant.image.width * common::Frame::kBytesPerPixel;
      ocr_crop.pixels.resize(static_cast<std::size_t>(ocr_crop.stride) *
                             ocr_crop.height);
      std::uint64_t luma_sum = 0U;
      for (const auto pixel : variant.image.pixels) {
        luma_sum += pixel;
      }
      const auto mean =
          variant.image.pixels.empty()
              ? 0U
              : static_cast<std::uint32_t>(luma_sum /
                                           variant.image.pixels.size());
      // White/gold titles sit on a dark card. Invert only dark crops so a
      // bright gold border does not become a solid black OCR input.
      const bool invert = mean < 140U;
      for (std::size_t index = 0U; index < variant.image.pixels.size();
           ++index) {
        const auto source = variant.image.pixels[index];
        const auto gray =
            invert ? static_cast<std::uint8_t>(255U - source) : source;
        const auto offset = index * common::Frame::kBytesPerPixel;
        ocr_crop.pixels[offset + 0U] = gray;
        ocr_crop.pixels[offset + 1U] = gray;
        ocr_crop.pixels[offset + 2U] = gray;
        ocr_crop.pixels[offset + 3U] = 255U;
      }
      if (!ocr_crop.IsValid()) {
        auto result = inner_.Recognize(title_crop);
        result.backend.append(
            ";title_preprocess=raw_fallback;diagnostic=invalid_bgra_adapter");
        return result;
      }
      auto result = inner_.Recognize(ocr_crop);
      result.backend.append(";title_preprocess=");
      result.backend.append(variant.id);
      result.backend.append(invert ? ";invert=1" : ";invert=0");
      result.backend.append(";strategy=single_default");
      return result;
    } catch (...) {
      auto result = inner_.Recognize(title_crop);
      try {
        result.backend.append(
            ";title_preprocess=raw_fallback;diagnostic=exception");
      } catch (...) {
      }
      return result;
    }
  }

 private:
  const vision::IOcrTitleRecognizer& inner_;
};

[[nodiscard]] std::unique_ptr<vision::IOcrTitleRecognizer>
MakeProductTitlePreprocessingOcr(const vision::IOcrTitleRecognizer& ocr) {
  return std::make_unique<ProductTitlePreprocessingOcr>(ocr);
}

[[nodiscard]] detector::AugmentScreenDetectorConfig EffectiveDetectorConfig(
    detector::AugmentScreenDetectorConfig config) {
  if (!config.layout.IsValid()) {
    using common::NormalizedRoi;
    config.layout = {NormalizedRoi{0.10, 0.10, 0.80, 0.80},
                     {NormalizedRoi{0.15, 0.20, 0.20, 0.55},
                      NormalizedRoi{0.40, 0.20, 0.20, 0.55},
                      NormalizedRoi{0.65, 0.20, 0.20, 0.55}}};
  }
  return config;
}

[[nodiscard]] common::NormalizedRoi Normalize(
    const detector::PixelRoi& roi, const common::Frame& frame) noexcept {
  return {static_cast<double>(roi.x) / static_cast<double>(frame.width),
          static_cast<double>(roi.y) / static_cast<double>(frame.height),
          static_cast<double>(roi.width) / static_cast<double>(frame.width),
          static_cast<double>(roi.height) / static_cast<double>(frame.height)};
}

[[nodiscard]] common::UtcTimestamp ObservedAt(
    const common::Frame& frame) noexcept {
  return frame.timestamps.captured_at_utc.value_or(
      common::UtcTimestamp::clock::now());
}

[[nodiscard]] std::uint8_t PixelLuma(const std::uint8_t* pixel) noexcept {
  const std::uint32_t blue = pixel[0U];
  const std::uint32_t green = pixel[1U];
  const std::uint32_t red = pixel[2U];
  return static_cast<std::uint8_t>((29U * blue + 150U * green + 77U * red) >>
                                   8U);
}

[[nodiscard]] bool IsStrongLexicalEvidence(
    const vision::CardRecognitionOutput& card) noexcept {
  // Icon ambiguity or absence must not erase exact/normalized lexical
  // provenance. Explicit icon conflicts are converted to Unknown before this
  // predicate runs, while fuzzy and icon-only outcomes do not have either
  // accepted prefix.
  return card.state == vision::CardRecognitionState::Recognized &&
         card.augment_id.has_value() &&
         (card.reason.starts_with("exact_match") ||
          card.reason.starts_with("normalized_match"));
}

[[nodiscard]] vision::TextMatchResult OcrIdentityForFusion(
    const vision::CardRecognitionOutput& card) {
  vision::TextMatchResult result;
  result.reason = card.reason;
  result.top1_score = card.match_confidence;
  if (card.state != vision::CardRecognitionState::Recognized ||
      !card.augment_id.has_value()) {
    return result;
  }
  result.id = card.augment_id;
  result.title = card.display_name;
  if (card.reason.starts_with("exact_match")) {
    result.kind = vision::TextMatchKind::Exact;
  } else if (card.reason.starts_with("normalized_match")) {
    result.kind = vision::TextMatchKind::Normalized;
  } else {
    result.kind = vision::TextMatchKind::Fuzzy;
  }
  return result;
}

[[nodiscard]] common::AugmentOfferObservation BuildOfferObservation(
    const common::Frame& frame,
    const detector::DetectorResult& recognition_detection,
    const vision::OfferRecognitionOutput& recognition,
    const bool force_recognition) {
  common::AugmentOfferObservation offer;
  const auto observed_at = ObservedAt(frame);
  const auto& rois = *recognition_detection.rois;

  common::AugmentScreenDetection detection;
  detection.state = common::DetectionState::Detected;
  detection.offer_roi = Normalize(rois.offer_region, frame);
  constexpr std::array slots{common::CardSlotId::Left,
                             common::CardSlotId::Center,
                             common::CardSlotId::Right};
  for (std::size_t index = 0U; index < slots.size(); ++index) {
    detection.card_slots[index] =
        common::CardSlot{slots[index], Normalize(rois.cards[index], frame)};
  }
  detection.metadata.source =
      force_recognition
          ? "augment_frame_processor:force_recognition_raw_detector"
          : "augment_frame_processor:stable_detector";
  detection.metadata.confidence =
      common::Confidence{recognition_detection.confidence};
  detection.metadata.observed_at = observed_at;
  offer.screen_detection = std::move(detection);

  float offer_confidence = recognition_detection.confidence;
  for (std::size_t index = 0U; index < recognition.cards.size(); ++index) {
    const auto& card = recognition.cards[index];
    common::AugmentRecognition recognized;
    recognized.state = common::RecognitionState::Recognized;
    recognized.slot = slots[index];
    recognized.augment_id = card.augment_id;
    recognized.display_name = card.display_name;
    recognized.metadata.source =
        force_recognition
            ? "augment_frame_processor:force_recognition"
            : "augment_frame_processor:recognition";
    recognized.metadata.confidence = common::Confidence{card.final_confidence};
    recognized.metadata.observed_at = observed_at;
    offer.recognitions[index] = std::move(recognized);
    offer_confidence = std::min(offer_confidence, card.final_confidence);
  }
  offer.metadata.source =
      force_recognition ? "augment_frame_processor:force_recognition_offer"
                        : "augment_frame_processor:offer";
  offer.metadata.confidence = common::Confidence{offer_confidence};
  offer.metadata.observed_at = observed_at;
  return offer;
}

[[nodiscard]] common::GameState ToGameState(
    const state::Phase1GameStateSnapshot& snapshot,
    const bool force_recognition, const std::uint32_t completed_offers) {
  common::GameState game_state;
  game_state.champion = snapshot.champion;
  game_state.offer_round = snapshot.offer_round;
  if (game_state.offer_round.has_value()) {
    *game_state.offer_round += completed_offers;
  }
  game_state.current_offer = snapshot.current_offer;
  for (const auto& selected : snapshot.selected_augments) {
    if (selected.has_value()) {
      game_state.selected_augments.push_back(*selected);
    }
  }
  game_state.metadata.source =
      force_recognition ? "augment_frame_processor:force_recognition_game_state"
                        : "augment_frame_processor:game_state";
  if (snapshot.current_offer.has_value()) {
    game_state.metadata.confidence =
        snapshot.current_offer->metadata.confidence;
    game_state.metadata.observed_at =
        snapshot.current_offer->metadata.observed_at;
  }
  return game_state;
}

[[nodiscard]] std::string_view ReducerReason(
    const state::ReducerErrorCode code) noexcept {
  using state::ReducerErrorCode;
  switch (code) {
    case ReducerErrorCode::Ok:
      return "offer_reduced";
    case ReducerErrorCode::NoChange:
      return "offer_unchanged";
    case ReducerErrorCode::InvalidChampion:
      return "invalid_champion";
    case ReducerErrorCode::InvalidObservation:
      return "invalid_offer";
    case ReducerErrorCode::IncompleteOffer:
      return "incomplete_offer";
    case ReducerErrorCode::LowConfidence:
      return "low_confidence";
    case ReducerErrorCode::DuplicateOffer:
      return "duplicate_offer";
    case ReducerErrorCode::OfferRoundLimitReached:
      return "offer_round_limit_reached";
    case ReducerErrorCode::NoCurrentOffer:
      return "no_current_offer";
    case ReducerErrorCode::SelectionUnconfirmed:
      return "selection_unconfirmed";
    case ReducerErrorCode::SelectionNotInCurrentOffer:
      return "selection_not_in_offer";
    case ReducerErrorCode::SelectionAlreadyConfirmed:
      return "selection_already_confirmed";
    case ReducerErrorCode::StalePreparedOffer:
      return "stale_prepared_offer";
  }
  return "reducer_rejected";
}

[[nodiscard]] std::string_view SessionReason(
    const SessionRuntimeError code) noexcept {
  switch (code) {
    case SessionRuntimeError::Ok:
      return "accepted_offer";
    case SessionRuntimeError::DuplicateOffer:
      return "duplicate_offer";
    case SessionRuntimeError::InvalidArgument:
      return "session_invalid_offer";
    case SessionRuntimeError::InvalidPath:
      return "session_invalid_path";
    case SessionRuntimeError::StorageError:
      return "session_storage_error";
    case SessionRuntimeError::ArtifactError:
      return "session_artifact_error";
    case SessionRuntimeError::SerializationError:
      return "session_serialization_error";
    case SessionRuntimeError::Closed:
      return "session_closed";
  }
  return "session_error";
}

}  // namespace

AugmentFrameProcessor::AugmentFrameProcessor(
    const vision::IOcrTitleRecognizer& ocr,
    std::vector<vision::TitleCandidate> mode_candidates,
    Phase1SessionRuntime& session_runtime,
    std::optional<std::string> manual_champion,
    const std::optional<std::size_t> selected_slot,
    detector::AugmentScreenDetectorConfig detector_config,
    detector::StableDetectorConfig confirmer_config,
    const float minimum_stable_confidence,
    std::vector<vision::IconHashTemplate> icon_templates,
    std::optional<std::string> icon_mode,
    const std::uint32_t completed_offers,
    const bool recognize_icons)
    : detector_(EffectiveDetectorConfig(std::move(detector_config))),
      confirmer_(confirmer_config),
      ocr_unconfirmed_rois_(confirmer_config.required_consecutive_frames <=
                            1U),
      preprocessing_ocr_(MakeProductTitlePreprocessingOcr(ocr)),
      recognition_pipeline_(*preprocessing_ocr_, std::move(mode_candidates)),
      icon_matcher_(std::move(icon_templates)),
      icon_mode_(std::move(icon_mode)),
      minimum_stable_confidence_(minimum_stable_confidence),
      completed_offers_(completed_offers),
      reducer_(minimum_stable_confidence),
      session_runtime_(&session_runtime),
      selected_slot_(selected_slot),
      recognize_icons_(recognize_icons) {
  Initialize(std::move(manual_champion));
}

AugmentFrameProcessor::AugmentFrameProcessor(
    const vision::IOcrTitleRecognizer& ocr,
    std::vector<vision::TitleCandidate> mode_candidates,
    IOfferSessionRuntime& session_runtime,
    std::optional<std::string> manual_champion,
    const std::optional<std::size_t> selected_slot,
    detector::AugmentScreenDetectorConfig detector_config,
    detector::StableDetectorConfig confirmer_config,
    const float minimum_stable_confidence,
    std::vector<vision::IconHashTemplate> icon_templates,
    std::optional<std::string> icon_mode,
    const std::uint32_t completed_offers,
    const bool recognize_icons)
    : detector_(EffectiveDetectorConfig(std::move(detector_config))),
      confirmer_(confirmer_config),
      ocr_unconfirmed_rois_(confirmer_config.required_consecutive_frames <=
                            1U),
      preprocessing_ocr_(MakeProductTitlePreprocessingOcr(ocr)),
      recognition_pipeline_(*preprocessing_ocr_, std::move(mode_candidates)),
      icon_matcher_(std::move(icon_templates)),
      icon_mode_(std::move(icon_mode)),
      minimum_stable_confidence_(minimum_stable_confidence),
      completed_offers_(completed_offers),
      reducer_(minimum_stable_confidence),
      injected_session_runtime_(&session_runtime),
      selected_slot_(selected_slot),
      recognize_icons_(recognize_icons) {
  Initialize(std::move(manual_champion));
}

void AugmentFrameProcessor::Initialize(
    std::optional<std::string> manual_champion) {
  if (!RuntimeIsOpen()) {
    throw std::invalid_argument("session runtime must be open");
  }
  if (selected_slot_.has_value() &&
      *selected_slot_ >= common::kAugmentCardCount) {
    throw std::invalid_argument("selected slot must be in the range [0, 2]");
  }
  if (completed_offers_ > state::kPhase1OfferRoundCount) {
    throw std::invalid_argument("completed offers must be in the range [0, 4]");
  }
  if (manual_champion.has_value()) {
    const auto champion_result =
        reducer_.SetManualChampion(std::move(*manual_champion));
    if (!champion_result.IsSuccess()) {
      throw std::invalid_argument("manual champion must not be blank");
    }
  }
}

FrameProcessResult AugmentFrameProcessor::Process(const common::Frame& frame) {
  return Process(frame, FrameProcessOptions{});
}

void AugmentFrameProcessor::RequestImmediateReread() noexcept {
  ResetContentAttempt();
  active_content_signature_.reset();
  pending_content_signature_.reset();
  pending_content_confirmation_frames_ = 0U;
}

FrameProcessResult AugmentFrameProcessor::Process(
    const common::Frame& frame, const FrameProcessOptions& options) {
  if (options.reread_offer) {
    RequestImmediateReread();
  }
  FrameProcessResult result;
  result.force_recognition = options.force_recognition;
  result.raw_detector = detector_.Detect(frame);
  result.stable_detector = confirmer_.Observe(result.raw_detector);
  result.rois = result.raw_detector.rois;
  if (options.reread_offer && !result.raw_detector.visible &&
      !options.allow_low_confidence_rois) {
    result.reason = "no_offer_on_screen";
    result.recommended_processing_hz = kIdleProcessingHz;
    return result;
  }
  const bool ocr_unconfirmed =
      (ocr_unconfirmed_rois_ || options.reread_offer ||
       options.allow_low_confidence_rois) &&
      result.raw_detector.rois.has_value();

  if (!result.raw_detector.visible && !ocr_unconfirmed) {
    consecutive_invisible_frames_ =
        std::min(consecutive_invisible_frames_ + 1U, kInvisibleFramesToRearm);
    if (consecutive_invisible_frames_ == kInvisibleFramesToRearm &&
        !dismissal_handled_) {
      const auto dismissed = reducer_.ScreenDismissed();
      dismissal_handled_ = true;
      active_content_signature_.reset();
      pending_content_signature_.reset();
      pending_content_confirmation_frames_ = 0U;
      ResetContentAttempt();
      if (dismissed.state_changed) {
        result.reason = "screen_dismissed_round_advanced";
      } else if (dismissed.code ==
                 state::ReducerErrorCode::OfferRoundLimitReached) {
        result.reason = "offer_round_limit_reached";
      } else {
        result.reason = result.raw_detector.reason;
      }
    } else {
      result.reason = result.raw_detector.reason;
    }
    result.recommended_processing_hz =
        options.force_recognition ? kActiveProcessingHz : kIdleProcessingHz;
    return result;
  }

  consecutive_invisible_frames_ = 0U;
  dismissal_handled_ = false;
  result.recommended_processing_hz = kActiveProcessingHz;
  detector::DetectorResult recognition_detector = result.stable_detector;
  if (options.force_recognition || ocr_unconfirmed) {
    recognition_detector = result.raw_detector;
    result.detector_stability_bypassed = !result.stable_detector.visible;
    // The recognition pipeline accepts only the stable-detector contract.
    // Force mode has already required a current-frame raw visible result with
    // real ROIs; retag only this local adapter while preserving the truthful
    // raw/stable diagnostics above.
    recognition_detector.visible = recognition_detector.rois.has_value();
    if (recognition_detector.visible) {
      recognition_detector.reason = "stable_visible";
    }
  }
  if (!recognition_detector.visible) {
    result.reason = recognition_detector.reason;
    return result;
  }

  if (!recognition_detector.rois.has_value()) {
    result.reason = options.force_recognition
                        ? "force_raw_detector_missing_rois"
                        : "stable_detector_missing_rois";
    return result;
  }
  result.rois = recognition_detector.rois;
  const auto signature =
      ComputeTitleContentSignature(frame, *recognition_detector.rois);
  if (!signature.has_value()) {
    result.reason = "content_signature_unavailable";
    return result;
  }

  if (options.reread_offer) {
    active_content_signature_ = *signature;
    pending_content_signature_.reset();
    pending_content_confirmation_frames_ = 0U;
  } else if (!active_content_signature_.has_value()) {
    active_content_signature_ = *signature;
    pending_content_signature_.reset();
    pending_content_confirmation_frames_ = 0U;
  } else if (IsSignificantContentChange(*active_content_signature_,
                                        *signature)) {
    if (pending_content_signature_.has_value() &&
        !IsSignificantContentChange(*pending_content_signature_, *signature)) {
      ++pending_content_confirmation_frames_;
    } else {
      pending_content_signature_ = *signature;
      pending_content_confirmation_frames_ = 1U;
    }
    if (pending_content_confirmation_frames_ <
        kContentChangeConfirmationFrames) {
      result.reason = "awaiting_content_change_confirmation:1/2";
      return result;
    }
    active_content_signature_ = *signature;
    pending_content_signature_.reset();
    pending_content_confirmation_frames_ = 0U;
    ResetContentAttempt();
  } else {
    pending_content_signature_.reset();
    pending_content_confirmation_frames_ = 0U;
  }

  if (content_processed_) {
    result.duplicate = true;
    result.reason = "same_content_already_processed";
    result.recognition = last_recognition_;
    result.recommended_processing_hz = kIdleProcessingHz;
    return result;
  }
  if (content_attempt_exhausted_ && !options.force_recognition) {
    result.reason = "content_retry_limit_reached";
    result.recognition = last_recognition_;
    result.recommended_processing_hz = kIdleProcessingHz;
    return result;
  }
  if (retry_backoff_frames_ > 0U && !options.force_recognition) {
    --retry_backoff_frames_;
    result.reason = "retry_backoff";
    return result;
  }
  // Exact/normalized lexical identity has already been checked against the
  // local catalog and any explicit OCR/icon conflict was converted to UNKNOWN.
  // The second physical frame still supplies temporal consensus through the
  // stable detector and unchanged title signature above; rerunning Windows
  // Media OCR on identical pixels only adds roughly one second on real frames.
  // Reuse the first frame's bounded recognition result instead.
  if (ocr_consensus_.has_value() &&
      ocr_consensus_->consecutive_frames + 1U >= kOcrConsensusFrames &&
      std::all_of(ocr_consensus_->strong_evidence.begin(),
                  ocr_consensus_->strong_evidence.end(),
                  [](const bool strong) { return strong; })) {
    ++ocr_consensus_->consecutive_frames;
    persistable_consensus_ = ocr_consensus_->recognition;
    result.recognition = ocr_consensus_->recognition;
    return PersistConsensus(std::move(result), frame, recognition_detector,
                            options.force_recognition);
  }
  if (persistable_consensus_.has_value()) {
    return PersistConsensus(std::move(result), frame, recognition_detector,
                            options.force_recognition);
  }

  auto recognition =
      recognition_pipeline_.Recognize(frame, recognition_detector);
  if (recognize_icons_) {
    ApplyIconRecognition(frame, *recognition_detector.rois, recognition);
  }
  result.recognition = recognition;
  last_recognition_ = recognition;
  result.ocr_executed = recognition.ocr_executed;
  if (!recognition.ocr_executed) {
    RegisterRetryableFailure(result, "recognition_not_executed");
    return result;
  }

  bool all_recognized = true;
  bool backend_unavailable = false;
  bool ocr_failed = false;
  for (const auto& card : recognition.cards) {
    all_recognized = all_recognized &&
                     card.state == vision::CardRecognitionState::Recognized;
    backend_unavailable =
        backend_unavailable ||
        card.state == vision::CardRecognitionState::BackendUnavailable;
    ocr_failed =
        ocr_failed || card.state == vision::CardRecognitionState::OcrFailed;
  }
  if (!all_recognized) {
    ocr_consensus_.reset();
    RegisterRetryableFailure(result, backend_unavailable
                                         ? "ocr_backend_unavailable"
                                     : ocr_failed ? "ocr_failed"
                                                  : "recognition_unknown");
    return result;
  }

  std::array<std::string, common::kAugmentCardCount> ids{};
  std::array<bool, common::kAugmentCardCount> strong_evidence{};
  for (std::size_t index = 0U; index < recognition.cards.size(); ++index) {
    if (!recognition.cards[index].augment_id.has_value()) {
      ocr_consensus_.reset();
      RegisterRetryableFailure(result, "recognition_unknown");
      return result;
    }
    ids[index] = *recognition.cards[index].augment_id;
    strong_evidence[index] = IsStrongLexicalEvidence(recognition.cards[index]);
  }

  if (!ocr_consensus_.has_value()) {
    ocr_consensus_ =
        OcrConsensus{std::move(ids), strong_evidence, recognition, 1U};
  } else if (ocr_consensus_->ids != ids) {
    ocr_consensus_ =
        OcrConsensus{std::move(ids), strong_evidence, recognition, 1U};
    RegisterRetryableFailure(result, "ocr_consensus_changed");
    return result;
  } else {
    ++ocr_consensus_->consecutive_frames;
    for (std::size_t index = 0U; index < strong_evidence.size(); ++index) {
      ocr_consensus_->strong_evidence[index] =
          ocr_consensus_->strong_evidence[index] || strong_evidence[index];
      if (strong_evidence[index] ||
          recognition.cards[index].final_confidence >
              ocr_consensus_->recognition.cards[index].final_confidence) {
        ocr_consensus_->recognition.cards[index] = recognition.cards[index];
      }
    }
  }

  if (ocr_consensus_->consecutive_frames < kOcrConsensusFrames &&
      !options.reread_offer) {
    result.reason = "awaiting_ocr_consensus:1/2";
    return result;
  }
  if (!std::all_of(ocr_consensus_->strong_evidence.begin(),
                   ocr_consensus_->strong_evidence.end(),
                   [](const bool strong) { return strong; })) {
    RegisterRetryableFailure(result, "fuzzy_requires_strong_consensus");
    return result;
  }

  persistable_consensus_ = ocr_consensus_->recognition;
  return PersistConsensus(std::move(result), frame, recognition_detector,
                          options.force_recognition);
}

void AugmentFrameProcessor::ApplyIconRecognition(
    const common::Frame& frame, const detector::ThreeCardRois& rois,
    vision::OfferRecognitionOutput& recognition) const {
  const auto mode = icon_mode_.has_value()
                        ? std::optional<std::string_view>{*icon_mode_}
                        : std::nullopt;
  for (std::size_t index = 0U; index < recognition.cards.size(); ++index) {
    auto& card = recognition.cards[index];
    const auto ocr_match = OcrIdentityForFusion(card);
    const auto icon_crop =
        detector::CropRawBgraOwning(frame, rois.icon_rects[index]);
    if (icon_crop.ok()) {
      card.icon_match = icon_matcher_.Match(*icon_crop.value, mode);
    } else {
      card.icon_match.state = vision::IconMatchState::Unknown;
      card.icon_match.reason = "icon_crop_failed:" + icon_crop.reason;
    }

    const auto fused = vision::FuseAugmentIdentity(ocr_match, card.icon_match);
    switch (fused.state) {
      case vision::FusedAugmentState::Confirmed:
        card.reason.append("+");
        card.reason.append(fused.reason);
        break;
      case vision::FusedAugmentState::Tentative:
        if (ocr_match.matched()) {
          card.reason.append("+");
          card.reason.append(fused.reason);
          card.reason.append(":");
          card.reason.append(card.icon_match.reason);
          const float original_confidence = card.final_confidence;
          float downgraded_confidence = std::clamp(
              original_confidence * kOcrOnlyIconConfidenceScale, 0.0F, 1.0F);
          if (ocr_match.kind == vision::TextMatchKind::Exact ||
              ocr_match.kind == vision::TextMatchKind::Normalized) {
            // Preserve liveness for strong lexical evidence that already met
            // the configured persistence threshold. This is especially
            // important for Windows Media OCR, whose no-confidence normalized
            // heuristic is 0.86 against the default 0.85 reducer threshold.
            downgraded_confidence = std::min(
                original_confidence,
                std::max(downgraded_confidence, minimum_stable_confidence_));
          }
          card.final_confidence = downgraded_confidence;
        } else {
          card.state = vision::CardRecognitionState::Unknown;
          card.augment_id = fused.augment_id;
          card.display_name.reset();
          card.final_confidence = 0.0F;
          card.reason = fused.reason;
        }
        break;
      case vision::FusedAugmentState::Unknown:
        if (fused.reason.starts_with("ocr_icon_conflict:")) {
          card.state = vision::CardRecognitionState::Unknown;
          card.augment_id.reset();
          card.display_name.reset();
          card.final_confidence = 0.0F;
        }
        card.reason = fused.reason;
        break;
    }
  }
}

void AugmentFrameProcessor::ResetContentAttempt() noexcept {
  ocr_consensus_.reset();
  persistable_consensus_.reset();
  last_recognition_.reset();
  retryable_failures_ = 0U;
  retry_backoff_frames_ = 0U;
  content_processed_ = false;
  content_attempt_exhausted_ = false;
}

void AugmentFrameProcessor::RegisterRetryableFailure(FrameProcessResult& result,
                                                     std::string reason) {
  result.reason = std::move(reason);
  retryable_failures_ =
      std::min(retryable_failures_ + 1U, kMaximumRetryableFailures);
  if (retryable_failures_ >= kMaximumRetryableFailures) {
    content_attempt_exhausted_ = true;
    retry_backoff_frames_ = 0U;
    result.recommended_processing_hz = kIdleProcessingHz;
    return;
  }
  retry_backoff_frames_ = 1U << (retryable_failures_ - 1U);
}

bool AugmentFrameProcessor::RuntimeIsOpen() const noexcept {
  if (session_runtime_ != nullptr) {
    return session_runtime_->IsOpen();
  }
  return injected_session_runtime_ != nullptr &&
         injected_session_runtime_->IsOpen();
}

AcceptedOfferResult AugmentFrameProcessor::PersistOffer(
    const common::GameState& state,
    const std::array<vision::CardRecognitionOutput, common::kAugmentCardCount>&
        card_outputs,
    const common::Frame& raw_frame, const detector::ThreeCardRois& rois,
    std::optional<std::string> selected_augment_id) {
  if (session_runtime_ != nullptr) {
    return session_runtime_->AcceptOffer(state, card_outputs, raw_frame, rois,
                                         std::move(selected_augment_id));
  }
  return injected_session_runtime_->AcceptOffer(
      state, card_outputs, raw_frame, rois, std::move(selected_augment_id));
}

FrameProcessResult AugmentFrameProcessor::PersistConsensus(
    FrameProcessResult result, const common::Frame& frame,
    const detector::DetectorResult& recognition_detector,
    const bool force_recognition) {
  const auto offer = BuildOfferObservation(
      frame, recognition_detector, *persistable_consensus_, force_recognition);
  std::optional<std::string> selected_augment_id;
  if (selected_slot_.has_value()) {
    selected_augment_id =
        persistable_consensus_->cards[*selected_slot_].augment_id;
  }

  auto prepared = reducer_.PrepareStableOffer(offer, selected_augment_id);
  if (!prepared.IsPrepared()) {
    result.duplicate =
        prepared.status.code == state::ReducerErrorCode::DuplicateOffer;
    result.reason = ReducerReason(prepared.status.code);
    if (result.duplicate) {
      content_processed_ = true;
      persistable_consensus_.reset();
      result.recommended_processing_hz = kIdleProcessingHz;
    } else {
      persistable_consensus_.reset();
      ocr_consensus_.reset();
      RegisterRetryableFailure(result, result.reason);
    }
    return result;
  }

  const auto relative_round = prepared.update->Snapshot().offer_round;
  if (!relative_round.has_value() ||
      *relative_round + completed_offers_ > state::kPhase1OfferRoundCount) {
    result.reason = "offer_round_limit_reached";
    result.recommended_processing_hz = kIdleProcessingHz;
    content_processed_ = true;
    persistable_consensus_.reset();
    ocr_consensus_.reset();
    return result;
  }

  auto persisted = PersistOffer(
      ToGameState(prepared.update->Snapshot(), force_recognition,
                  completed_offers_),
      persistable_consensus_->cards, frame, *recognition_detector.rois,
      selected_augment_id);
  result.accepted = persisted.accepted;
  result.duplicate = persisted.status.IsDuplicate();
  result.reason = SessionReason(persisted.status.code);
  if (!persisted.stdout_json.empty()) {
    result.offer_json = std::move(persisted.stdout_json);
  }

  if (!persisted.accepted) {
    if (result.duplicate) {
      content_processed_ = true;
      persistable_consensus_.reset();
      result.recommended_processing_hz = kIdleProcessingHz;
    } else {
      RegisterRetryableFailure(result, result.reason);
    }
    return result;
  }

  const auto committed =
      reducer_.CommitPreparedOffer(std::move(*prepared.update));
  if (!committed.IsSuccess()) {
    content_processed_ = true;
    result.reason = "reducer_commit_failed_after_persistence";
    result.recommended_processing_hz = kIdleProcessingHz;
    return result;
  }

  content_processed_ = true;
  persistable_consensus_.reset();
  ocr_consensus_.reset();
  retryable_failures_ = 0U;
  retry_backoff_frames_ = 0U;
  selected_slot_.reset();
  result.recommended_processing_hz = kIdleProcessingHz;
  return result;
}

std::optional<AugmentFrameProcessor::TitleContentSignature>
AugmentFrameProcessor::ComputeTitleContentSignature(
    const common::Frame& frame, const detector::ThreeCardRois& rois) noexcept {
  if (!frame.IsValid()) {
    return std::nullopt;
  }

  TitleContentSignature signature;
  for (std::size_t card_index = 0U; card_index < rois.cards.size();
       ++card_index) {
    const auto& card = rois.cards[card_index];
    if (!card.IsInside(frame.width, frame.height) || card.width == 0U ||
        card.height == 0U) {
      return std::nullopt;
    }
    const std::uint32_t title_height = std::max(1U, card.height / 3U);
    std::array<std::uint8_t, kTitleHashColumns * kTitleHashRows> samples{};
    std::uint32_t luma_sum = 0U;
    for (std::uint32_t row = 0U; row < kTitleHashRows; ++row) {
      for (std::uint32_t column = 0U; column < kTitleHashColumns; ++column) {
        const std::uint32_t x =
            card.x +
            std::min(card.width - 1U, ((2U * column + 1U) * card.width) /
                                          (2U * kTitleHashColumns));
        const std::uint32_t y =
            card.y +
            std::min(title_height - 1U,
                     ((2U * row + 1U) * title_height) / (2U * kTitleHashRows));
        const auto offset =
            static_cast<std::size_t>(y) * frame.stride +
            static_cast<std::size_t>(x) * common::Frame::kBytesPerPixel;
        const auto luma = PixelLuma(frame.buffer.data() + offset);
        samples[static_cast<std::size_t>(row) * kTitleHashColumns + column] =
            luma;
        luma_sum += luma;
      }
    }
    const auto mean = static_cast<std::uint8_t>(
        luma_sum / static_cast<std::uint32_t>(samples.size()));
    signature.mean_luma[card_index] = mean;
    std::uint64_t hash = 0U;
    for (std::size_t bit = 0U; bit < samples.size(); ++bit) {
      if (samples[bit] >= mean) {
        hash |= std::uint64_t{1U} << bit;
      }
    }
    signature.hashes[card_index] = hash;
  }
  return signature;
}

bool AugmentFrameProcessor::IsSignificantContentChange(
    const TitleContentSignature& left,
    const TitleContentSignature& right) noexcept {
  std::uint32_t changed_bits = 0U;
  std::uint32_t mean_luma_change = 0U;
  for (std::size_t index = 0U; index < left.hashes.size(); ++index) {
    changed_bits += static_cast<std::uint32_t>(
        std::popcount(left.hashes[index] ^ right.hashes[index]));
    mean_luma_change += static_cast<std::uint32_t>(
        std::abs(static_cast<int>(left.mean_luma[index]) -
                 static_cast<int>(right.mean_luma[index])));
  }
  return changed_bits >= kSignificantHashBitChanges ||
         mean_luma_change >= kSignificantMeanLumaChange;
}

}  // namespace lol_assistant::app
