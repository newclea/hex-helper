#include "lol_assistant/output/structured_json_writer.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>

#include "json_support.h"

namespace lol_assistant::output {
namespace {

void AppendOptionalString(std::string& output,
                          const std::optional<std::string>& value) {
  if (value.has_value()) {
    detail::AppendQuotedUtf8(output, *value);
  } else {
    output.append("null");
  }
}

void AppendMetadata(std::string& output,
                    const common::ObservationMetadata& metadata) {
  output.append("{\"source\":");
  detail::AppendQuotedUtf8(output, metadata.source);
  output.append(",\"confidence\":");
  detail::AppendFloating(output, metadata.confidence.value);
  output.append(",\"observed_at\":");
  if (metadata.observed_at.has_value()) {
    detail::AppendQuotedUtf8(output,
                             detail::FormatUtcTimestamp(*metadata.observed_at));
  } else {
    output.append("null");
  }
  output.push_back('}');
}

void AppendRoi(std::string& output, const common::NormalizedRoi& roi) {
  output.append("{\"x\":");
  detail::AppendFloating(output, roi.x);
  output.append(",\"y\":");
  detail::AppendFloating(output, roi.y);
  output.append(",\"width\":");
  detail::AppendFloating(output, roi.width);
  output.append(",\"height\":");
  detail::AppendFloating(output, roi.height);
  output.push_back('}');
}

void AppendCardSlotId(std::string& output, const common::CardSlotId slot) {
  switch (slot) {
    case common::CardSlotId::Unknown:
      output.append("null");
      break;
    case common::CardSlotId::Left:
      detail::AppendQuotedUtf8(output, "LEFT");
      break;
    case common::CardSlotId::Center:
      detail::AppendQuotedUtf8(output, "CENTER");
      break;
    case common::CardSlotId::Right:
      detail::AppendQuotedUtf8(output, "RIGHT");
      break;
  }
}

void AppendCardSlot(std::string& output, const common::CardSlot& slot) {
  output.append("{\"id\":");
  AppendCardSlotId(output, slot.id);
  output.append(",\"roi\":");
  AppendRoi(output, slot.roi);
  output.push_back('}');
}

void AppendDetectionState(std::string& output,
                          const common::DetectionState state) {
  switch (state) {
    case common::DetectionState::Unknown:
      detail::AppendQuotedUtf8(output, "UNKNOWN");
      break;
    case common::DetectionState::NotDetected:
      detail::AppendQuotedUtf8(output, "NOT_DETECTED");
      break;
    case common::DetectionState::Detected:
      detail::AppendQuotedUtf8(output, "DETECTED");
      break;
  }
}

void AppendRecognitionState(std::string& output,
                            const common::RecognitionState state) {
  switch (state) {
    case common::RecognitionState::Unknown:
      detail::AppendQuotedUtf8(output, "UNKNOWN");
      break;
    case common::RecognitionState::NotRecognized:
      detail::AppendQuotedUtf8(output, "NOT_RECOGNIZED");
      break;
    case common::RecognitionState::Recognized:
      detail::AppendQuotedUtf8(output, "RECOGNIZED");
      break;
  }
}

void AppendRecognition(std::string& output,
                       const common::AugmentRecognition& recognition) {
  output.append("{\"state\":");
  AppendRecognitionState(output, recognition.state);
  output.append(",\"slot\":");
  AppendCardSlotId(output, recognition.slot);
  output.append(",\"augment_id\":");
  AppendOptionalString(output, recognition.augment_id);
  output.append(",\"display_name\":");
  AppendOptionalString(output, recognition.display_name);
  output.append(",\"metadata\":");
  AppendMetadata(output, recognition.metadata);
  output.push_back('}');
}

void AppendRecognitionsArray(
    std::string& output,
    const std::array<std::optional<common::AugmentRecognition>,
                     common::kAugmentCardCount>& recognitions) {
  output.push_back('[');
  for (std::size_t index = 0U; index < recognitions.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    if (recognitions[index].has_value()) {
      AppendRecognition(output, *recognitions[index]);
    } else {
      output.append("null");
    }
  }
  output.push_back(']');
}

void AppendScreenDetection(std::string& output,
                           const common::AugmentScreenDetection& detection) {
  output.append("{\"state\":");
  AppendDetectionState(output, detection.state);
  output.append(",\"offer_roi\":");
  if (detection.offer_roi.has_value()) {
    AppendRoi(output, *detection.offer_roi);
  } else {
    output.append("null");
  }
  output.append(",\"card_slots\":[");
  for (std::size_t index = 0U; index < detection.card_slots.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    if (detection.card_slots[index].has_value()) {
      AppendCardSlot(output, *detection.card_slots[index]);
    } else {
      output.append("null");
    }
  }
  output.append("],\"metadata\":");
  AppendMetadata(output, detection.metadata);
  output.push_back('}');
}

void AppendOffer(std::string& output,
                 const common::AugmentOfferObservation& offer) {
  output.append("{\"screen_detection\":");
  if (offer.screen_detection.has_value()) {
    AppendScreenDetection(output, *offer.screen_detection);
  } else {
    output.append("null");
  }
  output.append(",\"recognitions\":");
  AppendRecognitionsArray(output, offer.recognitions);
  output.append(",\"metadata\":");
  AppendMetadata(output, offer.metadata);
  output.push_back('}');
}

[[nodiscard]] bool HasDuplicateKnownSlots(
    const std::array<std::optional<common::AugmentRecognition>,
                     common::kAugmentCardCount>& recognitions) {
  std::array<bool, 4U> seen{};
  for (const auto& recognition : recognitions) {
    if (!recognition.has_value() ||
        recognition->slot == common::CardSlotId::Unknown) {
      continue;
    }
    const auto index = static_cast<std::size_t>(recognition->slot);
    if (index >= seen.size() || seen[index]) {
      return true;
    }
    seen[index] = true;
  }
  return false;
}

}  // namespace

std::string StructuredJsonWriter::WriteGameState(
    const common::GameState& state) {
  if (!state.IsValid()) {
    throw std::invalid_argument("Cannot serialize an invalid GameState");
  }

  std::string output;
  output.reserve(1024U);
  output.append("{\"champion\":");
  AppendOptionalString(output, state.champion);
  output.append(",\"offer_round\":");
  if (state.offer_round.has_value()) {
    output.append(std::to_string(*state.offer_round));
  } else {
    output.append("null");
  }
  output.append(",\"selected_augments\":[");
  for (std::size_t index = 0U; index < state.selected_augments.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    AppendRecognition(output, state.selected_augments[index]);
  }
  output.append("],\"current_offer\":");
  if (state.current_offer.has_value()) {
    AppendOffer(output, *state.current_offer);
  } else {
    output.append("null");
  }
  output.append(",\"metadata\":");
  AppendMetadata(output, state.metadata);
  output.push_back('}');
  return output;
}

std::string StructuredJsonWriter::WriteScreenDetection(
    const common::AugmentScreenDetection& detection) {
  if (!detection.IsValid()) {
    throw std::invalid_argument(
        "Cannot serialize an invalid AugmentScreenDetection");
  }
  std::string output;
  output.reserve(512U);
  AppendScreenDetection(output, detection);
  return output;
}

std::string StructuredJsonWriter::WriteRecognitions(
    const std::array<std::optional<common::AugmentRecognition>,
                     common::kAugmentCardCount>& recognitions) {
  for (const auto& recognition : recognitions) {
    if (recognition.has_value() && !recognition->IsValid()) {
      throw std::invalid_argument(
          "Cannot serialize an invalid AugmentRecognition");
    }
  }
  if (HasDuplicateKnownSlots(recognitions)) {
    throw std::invalid_argument(
        "Cannot serialize duplicate known recognition slots");
  }
  std::string output;
  output.reserve(768U);
  output.append("{\"recognitions\":");
  AppendRecognitionsArray(output, recognitions);
  output.push_back('}');
  return output;
}

}  // namespace lol_assistant::output
