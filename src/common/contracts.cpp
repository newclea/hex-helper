#include "lol_assistant/common/contracts.h"

#include <array>
#include <cmath>
#include <limits>

namespace lol_assistant::common {
namespace {

[[nodiscard]] bool IsKnownCardSlot(const CardSlotId id) noexcept {
  return id == CardSlotId::Left || id == CardSlotId::Center ||
         id == CardSlotId::Right;
}

[[nodiscard]] std::size_t CardSlotIndex(const CardSlotId id) noexcept {
  return static_cast<std::size_t>(id);
}

[[nodiscard]] bool IsNonEmpty(const std::optional<std::string>& value) noexcept {
  return value.has_value() && !value->empty();
}

}  // namespace

bool Confidence::IsValid() const noexcept {
  return std::isfinite(value) && value >= 0.0F && value <= 1.0F;
}

std::optional<Confidence> Confidence::TryCreate(const float value) noexcept {
  const Confidence candidate{value};
  if (!candidate.IsValid()) {
    return std::nullopt;
  }
  return candidate;
}

bool FrameTimestamps::IsValid() const noexcept {
  return !capture_started.has_value() || !capture_completed.has_value() ||
         *capture_started <= *capture_completed;
}

bool ObservationMetadata::IsValid() const noexcept {
  if (source.empty() || !confidence.IsValid()) {
    return false;
  }
  return !IsStub() || confidence.value == 0.0F;
}

bool ObservationMetadata::IsStub() const noexcept {
  return source == kStubObservationSource;
}

ObservationMetadata ObservationMetadata::Stub() {
  ObservationMetadata metadata;
  metadata.source = kStubObservationSource;
  metadata.confidence = Confidence{};
  metadata.observed_at = std::nullopt;
  return metadata;
}

bool NormalizedRoi::IsValid() const noexcept {
  if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(width) ||
      !std::isfinite(height)) {
    return false;
  }

  return x >= 0.0 && y >= 0.0 && width > 0.0 && height > 0.0 && x <= 1.0 &&
         y <= 1.0 && width <= (1.0 - x) && height <= (1.0 - y);
}

bool CardSlot::IsValid() const noexcept {
  return IsKnownCardSlot(id) && roi.IsValid();
}

bool FrameSource::IsValid() const noexcept {
  switch (kind) {
    case FrameSourceKind::Unknown:
      return id.empty();
    case FrameSourceKind::Stub:
      return id == kStubObservationSource;
    case FrameSourceKind::WindowsGraphicsCapture:
    case FrameSourceKind::DesktopDuplication:
    case FrameSourceKind::Replay:
      return !id.empty();
  }
  return false;
}

std::optional<std::size_t> Frame::RequiredBufferSize() const noexcept {
  if (width == 0U || height == 0U || stride == 0U) {
    return std::nullopt;
  }

  constexpr auto max_size = std::numeric_limits<std::size_t>::max();
  if (static_cast<std::size_t>(width) > max_size / kBytesPerPixel) {
    return std::nullopt;
  }

  const auto minimum_stride = static_cast<std::size_t>(width) * kBytesPerPixel;
  if (static_cast<std::size_t>(stride) < minimum_stride) {
    return std::nullopt;
  }

  if (static_cast<std::size_t>(height) >
      max_size / static_cast<std::size_t>(stride)) {
    return std::nullopt;
  }

  return static_cast<std::size_t>(stride) * static_cast<std::size_t>(height);
}

bool Frame::IsValid() const noexcept {
  const auto required_size = RequiredBufferSize();
  return source.kind != FrameSourceKind::Unknown && source.IsValid() &&
         timestamps.IsValid() && required_size.has_value() &&
         buffer.size() == *required_size;
}

bool AugmentScreenDetection::IsValid() const noexcept {
  if (!metadata.IsValid()) {
    return false;
  }

  const bool has_any_slot =
      card_slots[0].has_value() || card_slots[1].has_value() ||
      card_slots[2].has_value();

  if (state == DetectionState::Unknown || state == DetectionState::NotDetected) {
    return !offer_roi.has_value() && !has_any_slot;
  }

  if (state != DetectionState::Detected || !offer_roi.has_value() ||
      !offer_roi->IsValid()) {
    return false;
  }

  std::array<bool, 4U> seen{};
  for (const auto& slot : card_slots) {
    if (!slot.has_value() || !slot->IsValid()) {
      return false;
    }
    const auto index = CardSlotIndex(slot->id);
    if (seen[index]) {
      return false;
    }
    seen[index] = true;
  }

  return seen[CardSlotIndex(CardSlotId::Left)] &&
         seen[CardSlotIndex(CardSlotId::Center)] &&
         seen[CardSlotIndex(CardSlotId::Right)];
}

bool AugmentRecognition::IsValid() const noexcept {
  if (!metadata.IsValid()) {
    return false;
  }

  if (display_name.has_value() && display_name->empty()) {
    return false;
  }

  switch (state) {
    case RecognitionState::Unknown:
      return slot == CardSlotId::Unknown && !augment_id.has_value() &&
             !display_name.has_value();
    case RecognitionState::NotRecognized:
      return IsKnownCardSlot(slot) && !augment_id.has_value() &&
             !display_name.has_value();
    case RecognitionState::Recognized:
      return IsKnownCardSlot(slot) && IsNonEmpty(augment_id);
  }
  return false;
}

bool AugmentOfferObservation::IsValid() const noexcept {
  if (!metadata.IsValid() ||
      (screen_detection.has_value() && !screen_detection->IsValid())) {
    return false;
  }

  std::array<bool, 4U> seen{};
  bool has_recognition = false;
  for (const auto& recognition : recognitions) {
    if (!recognition.has_value()) {
      continue;
    }
    has_recognition = true;
    if (!recognition->IsValid() ||
        !IsKnownCardSlot(recognition->slot)) {
      return false;
    }
    const auto index = CardSlotIndex(recognition->slot);
    if (seen[index]) {
      return false;
    }
    seen[index] = true;
  }

  if (has_recognition) {
    return screen_detection.has_value() &&
           screen_detection->state == DetectionState::Detected;
  }
  return true;
}

bool GameState::IsValid() const noexcept {
  if (!metadata.IsValid() || (champion.has_value() && champion->empty()) ||
      (offer_round.has_value() && *offer_round == 0U) ||
      (current_offer.has_value() && !current_offer->IsValid())) {
    return false;
  }

  for (const auto& augment : selected_augments) {
    if (!augment.IsValid() || augment.state != RecognitionState::Recognized) {
      return false;
    }
  }
  return true;
}

}  // namespace lol_assistant::common
