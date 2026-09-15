#include "lol_assistant/output/debug_artifact_writer.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <future>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "json_support.h"
#include "lol_assistant/replay/wic_image_codec.h"

namespace lol_assistant::output {
namespace {

[[nodiscard]] std::string SanitizePathComponent(const std::string_view input) {
  std::string output;
  output.reserve(input.size());
  for (const unsigned char character : input) {
    const bool allowed = (character >= 'a' && character <= 'z') ||
                         (character >= 'A' && character <= 'Z') ||
                         (character >= '0' && character <= '9') ||
                         character == '-' || character == '_' ||
                         character == '.';
    output.push_back(allowed ? static_cast<char>(character) : '_');
  }
  if (output.empty()) {
    output = "session";
  }
  if (output == "." || output == "..") {
    output.insert(output.begin(), '_');
  }
  return output;
}

[[nodiscard]] bool IsUnderRoot(const std::filesystem::path &root,
                               const std::filesystem::path &candidate) {
  const auto normalized_root = root.lexically_normal();
  const auto normalized_candidate = candidate.lexically_normal();
  auto root_part = normalized_root.begin();
  auto candidate_part = normalized_candidate.begin();
  for (; root_part != normalized_root.end(); ++root_part, ++candidate_part) {
    if (candidate_part == normalized_candidate.end() ||
        *root_part != *candidate_part) {
      return false;
    }
  }
  return candidate_part != normalized_candidate.end();
}

[[nodiscard]] std::size_t SlotArrayIndex(const common::CardSlotId id) {
  switch (id) {
  case common::CardSlotId::Left:
    return 0U;
  case common::CardSlotId::Center:
    return 1U;
  case common::CardSlotId::Right:
    return 2U;
  case common::CardSlotId::Unknown:
    break;
  }
  throw std::invalid_argument("Debug artifact card slot is UNKNOWN");
}

[[nodiscard]] std::array<common::CardSlot, common::kAugmentCardCount>
CanonicalSlots(const DebugArtifactRequest &request) {
  std::array<common::CardSlot, common::kAugmentCardCount> slots{};
  std::array<bool, common::kAugmentCardCount> seen{};
  for (const auto &slot : request.card_slots) {
    if (!slot.IsValid()) {
      throw std::invalid_argument("Debug artifact request has an invalid ROI");
    }
    const std::size_t index = SlotArrayIndex(slot.id);
    if (seen[index]) {
      throw std::invalid_argument(
          "Debug artifact request has duplicate card slots");
    }
    seen[index] = true;
    slots[index] = slot;
  }
  if (!std::all_of(seen.begin(), seen.end(),
                   [](const bool value) { return value; })) {
    throw std::invalid_argument(
        "Debug artifact request must include LEFT/CENTER/RIGHT ROIs");
  }
  return slots;
}

[[nodiscard]] common::Frame CropFrame(const common::Frame &source,
                                      const common::NormalizedRoi &roi) {
  const double source_width = static_cast<double>(source.width);
  const double source_height = static_cast<double>(source.height);
  const auto left = static_cast<std::uint32_t>(
      std::clamp(std::floor(roi.x * source_width), 0.0, source_width - 1.0));
  const auto top = static_cast<std::uint32_t>(
      std::clamp(std::floor(roi.y * source_height), 0.0, source_height - 1.0));
  const auto right = static_cast<std::uint32_t>(
      std::clamp(std::ceil((roi.x + roi.width) * source_width),
                 static_cast<double>(left + 1U), source_width));
  const auto bottom = static_cast<std::uint32_t>(
      std::clamp(std::ceil((roi.y + roi.height) * source_height),
                 static_cast<double>(top + 1U), source_height));

  common::Frame crop;
  crop.source = source.source;
  crop.frame_id = source.frame_id;
  crop.timestamps = source.timestamps;
  crop.width = right - left;
  crop.height = bottom - top;
  crop.stride =
      crop.width * static_cast<std::uint32_t>(common::Frame::kBytesPerPixel);
  crop.buffer.resize(static_cast<std::size_t>(crop.stride) * crop.height);
  for (std::uint32_t row = 0U; row < crop.height; ++row) {
    const auto *source_row =
        source.buffer.data() +
        static_cast<std::size_t>(top + row) * source.stride +
        static_cast<std::size_t>(left) * common::Frame::kBytesPerPixel;
    auto *destination_row =
        crop.buffer.data() + static_cast<std::size_t>(row) * crop.stride;
    std::copy_n(source_row, crop.stride, destination_row);
  }
  if (!crop.IsValid()) {
    throw std::runtime_error("ROI cropping produced an invalid frame");
  }
  return crop;
}

[[nodiscard]] std::string TriggerName(const DebugArtifactTrigger trigger) {
  switch (trigger) {
  case DebugArtifactTrigger::LowConfidence:
    return "LOW_CONFIDENCE";
  case DebugArtifactTrigger::Conflict:
    return "CONFLICT";
  case DebugArtifactTrigger::Manual:
    return "MANUAL";
  }
  throw std::invalid_argument("Unknown debug artifact trigger");
}

void AppendPathFilename(std::string &json, const std::filesystem::path &path) {
  const auto encoded = path.filename().u8string();
  detail::AppendQuotedUtf8(
      json, std::string_view(reinterpret_cast<const char *>(encoded.data()),
                             encoded.size()));
}

void WriteSidecar(const DebugArtifactSet &artifacts,
                  const DebugArtifactRequest &request) {
  std::string json;
  json.reserve(1024U);
  json.append("{\"session_id\":");
  detail::AppendQuotedUtf8(json, request.session_id);
  json.append(",\"timestamp\":");
  detail::AppendQuotedUtf8(json, detail::FormatUtcTimestamp(request.timestamp));
  json.append(",\"confidence\":");
  detail::AppendFloating(json, request.confidence.value);
  json.append(",\"backend\":");
  detail::AppendQuotedUtf8(json, request.backend);
  json.append(",\"catalog_version\":");
  detail::AppendQuotedUtf8(json, request.catalog_version);
  json.append(",\"trigger\":");
  detail::AppendQuotedUtf8(json, TriggerName(request.trigger));
  json.append(",\"artifacts\":{\"raw\":");
  AppendPathFilename(json, artifacts.raw_frame);
  json.append(",\"LEFT\":");
  AppendPathFilename(json, artifacts.card_rois[0U]);
  json.append(",\"CENTER\":");
  AppendPathFilename(json, artifacts.card_rois[1U]);
  json.append(",\"RIGHT\":");
  AppendPathFilename(json, artifacts.card_rois[2U]);
  json.append("}}\n");

  std::ofstream output(artifacts.sidecar_json,
                       std::ios::binary | std::ios::out);
  if (!output) {
    throw std::runtime_error("Unable to create artifact sidecar: " +
                             artifacts.sidecar_json.string());
  }
  output.write(json.data(), static_cast<std::streamsize>(json.size()));
  if (!output) {
    throw std::runtime_error("Failed while writing artifact sidecar: " +
                             artifacts.sidecar_json.string());
  }
}

} // namespace

DefaultDebugArtifactPolicy::DefaultDebugArtifactPolicy(
    const float low_confidence_threshold, const bool persist_conflicts,
    const bool persist_manual)
    : low_confidence_threshold_(low_confidence_threshold),
      persist_conflicts_(persist_conflicts), persist_manual_(persist_manual) {
  if (!common::Confidence{low_confidence_threshold_}.IsValid()) {
    throw std::invalid_argument(
        "Low-confidence artifact threshold must be in [0, 1]");
  }
}

bool DefaultDebugArtifactPolicy::ShouldPersist(
    const DebugArtifactPolicyContext &context) const {
  if (!context.confidence.IsValid()) {
    return false;
  }
  switch (context.trigger) {
  case DebugArtifactTrigger::LowConfidence:
    return context.confidence.value <= low_confidence_threshold_;
  case DebugArtifactTrigger::Conflict:
    return persist_conflicts_;
  case DebugArtifactTrigger::Manual:
    return persist_manual_;
  }
  return false;
}

DebugArtifactWriter::DebugArtifactWriter(std::filesystem::path output_root) {
  if (output_root.empty()) {
    throw std::invalid_argument("Debug artifact output root cannot be empty");
  }
  output_root_ =
      std::filesystem::absolute(std::move(output_root)).lexically_normal();
  std::filesystem::create_directories(output_root_);
  output_root_ = std::filesystem::weakly_canonical(output_root_);
  if (!std::filesystem::is_directory(output_root_)) {
    throw std::runtime_error("Debug artifact output root is not a directory");
  }
}

const std::filesystem::path &DebugArtifactWriter::OutputRoot() const noexcept {
  return output_root_;
}

std::optional<DebugArtifactSet> DebugArtifactWriter::WriteIfRequested(
    const common::Frame &frame, const DebugArtifactRequest &request,
    const IDebugArtifactPolicy &policy) const {
  if (!frame.IsValid()) {
    throw std::invalid_argument("Cannot persist an invalid debug frame");
  }
  if (request.session_id.empty() || request.backend.empty() ||
      request.catalog_version.empty() || !request.confidence.IsValid()) {
    throw std::invalid_argument(
        "Debug artifact metadata is incomplete or invalid");
  }
  const auto slots = CanonicalSlots(request);
  const DebugArtifactPolicyContext context{request.trigger, request.confidence};
  if (!policy.ShouldPersist(context)) {
    return std::nullopt;
  }

  const std::string safe_session = SanitizePathComponent(request.session_id);
  const std::filesystem::path session_directory =
      (output_root_ / safe_session).lexically_normal();
  if (!IsUnderRoot(output_root_, session_directory)) {
    throw std::runtime_error("Artifact session path escaped the output root");
  }
  std::filesystem::create_directories(session_directory);
  if (!IsUnderRoot(output_root_,
                   std::filesystem::weakly_canonical(session_directory))) {
    throw std::runtime_error("Artifact session directory escaped output root");
  }

  using namespace std::chrono;
  const auto unix_microseconds =
      duration_cast<microseconds>(request.timestamp.time_since_epoch()).count();

  DebugArtifactSet artifacts;
  std::string prefix;
  for (std::size_t attempt = 0U; attempt < 1024U; ++attempt) {
    const std::uint64_t sequence =
        sequence_.fetch_add(1U, std::memory_order_relaxed);
    prefix = std::to_string(unix_microseconds) + "_f" +
             std::to_string(frame.frame_id) + "_" + std::to_string(sequence);
    artifacts.raw_frame = session_directory / (prefix + "_RAW.png");
    if (!std::filesystem::exists(artifacts.raw_frame)) {
      break;
    }
    prefix.clear();
  }
  if (prefix.empty()) {
    throw std::runtime_error("Unable to reserve a unique artifact filename");
  }
  artifacts.card_rois[0U] = session_directory / (prefix + "_LEFT.png");
  artifacts.card_rois[1U] = session_directory / (prefix + "_CENTER.png");
  artifacts.card_rois[2U] = session_directory / (prefix + "_RIGHT.png");
  artifacts.sidecar_json = session_directory / (prefix + ".json");

  const std::array<std::filesystem::path, 5U> targets{
      artifacts.raw_frame, artifacts.card_rois[0U], artifacts.card_rois[1U],
      artifacts.card_rois[2U], artifacts.sidecar_json};
  for (const auto &target : targets) {
    if (!IsUnderRoot(output_root_, target)) {
      throw std::runtime_error("Artifact target escaped the output root");
    }
  }

  try {
    std::array<common::Frame, common::kAugmentCardCount> crops{};
    for (std::size_t index = 0U; index < slots.size(); ++index) {
      crops[index] = CropFrame(frame, slots[index].roi);
    }
    std::array<std::future<void>, common::kAugmentCardCount + 1U> writes{};
    writes[0U] = std::async(std::launch::async, [&frame, &artifacts] {
      replay::WicImageCodec::SavePng(frame, artifacts.raw_frame);
    });
    for (std::size_t index = 0U; index < crops.size(); ++index) {
      writes[index + 1U] =
          std::async(std::launch::async, [&crops, &artifacts, index] {
            replay::WicImageCodec::SavePng(crops[index],
                                           artifacts.card_rois[index]);
          });
    }
    std::exception_ptr write_failure;
    for (auto &write : writes) {
      try {
        write.get();
      } catch (...) {
        if (write_failure == nullptr) {
          write_failure = std::current_exception();
        }
      }
    }
    if (write_failure != nullptr) {
      std::rethrow_exception(write_failure);
    }
    WriteSidecar(artifacts, request);
  } catch (...) {
    for (const auto &target : targets) {
      std::error_code ignored;
      std::filesystem::remove(target, ignored);
    }
    throw;
  }
  return artifacts;
}

} // namespace lol_assistant::output
