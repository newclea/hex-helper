#include "lol_assistant/collection/sample_collector.h"

#include <Windows.h>

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cwctype>
#include <fstream>
#include <iterator>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "lol_assistant/replay/wic_image_codec.h"
#include "lol_assistant/vision/icon_matcher.h"

#include "../output/json_support.h"

namespace lol_assistant::collection {
namespace {

constexpr std::array<std::wstring_view, 5U> kRequiredFiles{
    L"RAW.png", L"LEFT_CARD.png", L"CENTER_CARD.png", L"RIGHT_CARD.png",
    L"metadata.json"};

class SerializationFailure final : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

class WicSampleImageWriter final : public ISampleImageWriter {
 public:
  void SavePng(const common::Frame& frame,
               const std::filesystem::path& path) const override {
    replay::WicImageCodec::SavePng(frame, path);
  }
};

class JsonSyntaxValidator final {
 public:
  explicit JsonSyntaxValidator(const std::string_view input) : input_(input) {}

  [[nodiscard]] bool Parse() noexcept {
    try {
      SkipWhitespace();
      if (!ParseValue(0U)) {
        return false;
      }
      SkipWhitespace();
      return position_ == input_.size();
    } catch (...) {
      return false;
    }
  }

 private:
  static constexpr std::size_t kMaximumDepth = 64U;

  void SkipWhitespace() noexcept {
    while (position_ < input_.size()) {
      const char value = input_[position_];
      if (value != ' ' && value != '\t' && value != '\r' && value != '\n') {
        break;
      }
      ++position_;
    }
  }

  [[nodiscard]] bool ParseValue(const std::size_t depth) {
    if (depth > kMaximumDepth || position_ >= input_.size()) {
      return false;
    }
    switch (input_[position_]) {
      case '{':
        return ParseObject(depth + 1U);
      case '[':
        return ParseArray(depth + 1U);
      case '"':
        return ParseString();
      case 't':
        return ParseLiteral("true");
      case 'f':
        return ParseLiteral("false");
      case 'n':
        return ParseLiteral("null");
      default:
        return ParseNumber();
    }
  }

  [[nodiscard]] bool ParseObject(const std::size_t depth) {
    ++position_;
    SkipWhitespace();
    if (Consume('}')) {
      return true;
    }
    while (position_ < input_.size()) {
      if (!ParseString()) {
        return false;
      }
      SkipWhitespace();
      if (!Consume(':')) {
        return false;
      }
      SkipWhitespace();
      if (!ParseValue(depth)) {
        return false;
      }
      SkipWhitespace();
      if (Consume('}')) {
        return true;
      }
      if (!Consume(',')) {
        return false;
      }
      SkipWhitespace();
    }
    return false;
  }

  [[nodiscard]] bool ParseArray(const std::size_t depth) {
    ++position_;
    SkipWhitespace();
    if (Consume(']')) {
      return true;
    }
    while (position_ < input_.size()) {
      if (!ParseValue(depth)) {
        return false;
      }
      SkipWhitespace();
      if (Consume(']')) {
        return true;
      }
      if (!Consume(',')) {
        return false;
      }
      SkipWhitespace();
    }
    return false;
  }

  [[nodiscard]] bool ParseString() {
    if (!Consume('"')) {
      return false;
    }
    while (position_ < input_.size()) {
      const auto value = static_cast<unsigned char>(input_[position_++]);
      if (value == static_cast<unsigned char>('"')) {
        return true;
      }
      if (value < 0x20U) {
        return false;
      }
      if (value != static_cast<unsigned char>('\\')) {
        continue;
      }
      if (position_ >= input_.size()) {
        return false;
      }
      const char escape = input_[position_++];
      if (escape == '"' || escape == '\\' || escape == '/' ||
          escape == 'b' || escape == 'f' || escape == 'n' || escape == 'r' ||
          escape == 't') {
        continue;
      }
      if (escape != 'u' || input_.size() - position_ < 4U) {
        return false;
      }
      for (std::size_t index = 0U; index < 4U; ++index) {
        const char digit = input_[position_++];
        const bool hexadecimal =
            (digit >= '0' && digit <= '9') ||
            (digit >= 'a' && digit <= 'f') ||
            (digit >= 'A' && digit <= 'F');
        if (!hexadecimal) {
          return false;
        }
      }
    }
    return false;
  }

  [[nodiscard]] bool ParseNumber() noexcept {
    const std::size_t start = position_;
    static_cast<void>(Consume('-'));
    if (Consume('0')) {
      if (position_ < input_.size() && input_[position_] >= '0' &&
          input_[position_] <= '9') {
        return false;
      }
    } else {
      if (!ConsumeDigit('1', '9')) {
        position_ = start;
        return false;
      }
      while (ConsumeDigit('0', '9')) {
      }
    }
    if (Consume('.')) {
      if (!ConsumeDigit('0', '9')) {
        return false;
      }
      while (ConsumeDigit('0', '9')) {
      }
    }
    if (position_ < input_.size() &&
        (input_[position_] == 'e' || input_[position_] == 'E')) {
      ++position_;
      if (position_ < input_.size() &&
          (input_[position_] == '+' || input_[position_] == '-')) {
        ++position_;
      }
      if (!ConsumeDigit('0', '9')) {
        return false;
      }
      while (ConsumeDigit('0', '9')) {
      }
    }
    return position_ > start;
  }

  [[nodiscard]] bool ParseLiteral(const std::string_view literal) noexcept {
    if (input_.substr(position_, literal.size()) != literal) {
      return false;
    }
    position_ += literal.size();
    return true;
  }

  [[nodiscard]] bool Consume(const char expected) noexcept {
    if (position_ >= input_.size() || input_[position_] != expected) {
      return false;
    }
    ++position_;
    return true;
  }

  [[nodiscard]] bool ConsumeDigit(const char minimum,
                                  const char maximum) noexcept {
    if (position_ >= input_.size() || input_[position_] < minimum ||
        input_[position_] > maximum) {
      return false;
    }
    ++position_;
    return true;
  }

  std::string_view input_;
  std::size_t position_{0U};
};

[[nodiscard]] bool HasDotDotComponent(
    const std::filesystem::path& path) noexcept {
  for (const auto& component : path) {
    if (component == L"..") {
      return true;
    }
  }
  return false;
}

[[nodiscard]] bool IsDescendantPath(const std::filesystem::path& root,
                                    const std::filesystem::path& candidate) {
  const auto relative = candidate.lexically_normal().lexically_relative(root);
  if (relative.empty() || relative.is_absolute()) {
    return false;
  }
  for (const auto& component : relative) {
    if (component == L"..") {
      return false;
    }
  }
  return true;
}

void RequireDescendantPath(const std::filesystem::path& root,
                           const std::filesystem::path& candidate) {
  if (!IsDescendantPath(root, candidate)) {
    throw std::invalid_argument("sample output escaped dataset root");
  }
}

[[nodiscard]] bool Contains(const detector::PixelRoi& outer,
                            const detector::PixelRoi& inner) noexcept {
  const auto outer_right =
      static_cast<std::uint64_t>(outer.x) + outer.width;
  const auto outer_bottom =
      static_cast<std::uint64_t>(outer.y) + outer.height;
  const auto inner_right =
      static_cast<std::uint64_t>(inner.x) + inner.width;
  const auto inner_bottom =
      static_cast<std::uint64_t>(inner.y) + inner.height;
  return inner.x >= outer.x && inner.y >= outer.y &&
         inner_right <= outer_right && inner_bottom <= outer_bottom;
}

[[nodiscard]] bool IsKnown(const SampleKind kind) noexcept {
  return kind == SampleKind::Real || kind == SampleKind::Synthetic ||
         kind == SampleKind::Unknown;
}

[[nodiscard]] bool IsKnown(const CaptureReason reason) noexcept {
  return reason == CaptureReason::DetectorSuspect ||
         reason == CaptureReason::ManualF8;
}

[[nodiscard]] bool IsKnown(const OcrSampleStatus status) noexcept {
  return status == OcrSampleStatus::NotRun ||
         status == OcrSampleStatus::BackendUnavailable ||
         status == OcrSampleStatus::Failed ||
         status == OcrSampleStatus::Unknown ||
         status == OcrSampleStatus::Matched;
}

[[nodiscard]] bool HasText(const std::string& value) noexcept {
  return std::ranges::any_of(value, [](const unsigned char character) {
    return std::isspace(character) == 0;
  });
}

[[nodiscard]] bool HasText(
    const std::optional<std::string>& value) noexcept {
  return value.has_value() && HasText(*value);
}

[[nodiscard]] bool IsLiveCaptureSource(
    const common::FrameSourceKind kind) noexcept {
  return kind == common::FrameSourceKind::WindowsGraphicsCapture ||
         kind == common::FrameSourceKind::DesktopDuplication;
}

[[nodiscard]] std::optional<SampleKind> DatasetBucketKind(
    const std::filesystem::path& root) {
  std::wstring name = root.filename().wstring();
  std::ranges::transform(name, name.begin(), [](const wchar_t character) {
    return static_cast<wchar_t>(std::towlower(character));
  });
  if (name == L"real") {
    return SampleKind::Real;
  }
  if (name == L"synthetic") {
    return SampleKind::Synthetic;
  }
  if (name == L"unknown") {
    return SampleKind::Unknown;
  }
  return std::nullopt;
}

[[nodiscard]] std::optional<std::string> ValidateRequest(
    const common::Frame& frame, const SampleCollectionRequest& request) {
  if (!frame.IsValid()) {
    return "raw frame is invalid";
  }
  if (!IsKnown(request.sample_kind) || !IsKnown(request.capture_reason)) {
    return "sample kind or capture reason is invalid";
  }
  if (!HasText(frame.source.id)) {
    return "frame source reference must be non-empty";
  }
  const bool live_capture = IsLiveCaptureSource(frame.source.kind);
  if (request.sample_kind == SampleKind::Real && !live_capture) {
    return "real samples require a live capture frame source";
  }
  if (request.sample_kind != SampleKind::Real && live_capture) {
    return "live capture frame sources must use real provenance";
  }
  if (frame.source.kind == common::FrameSourceKind::Replay &&
      request.sample_kind != SampleKind::Synthetic &&
      request.sample_kind != SampleKind::Unknown) {
    return "replay frame sources require synthetic or unknown provenance";
  }
  if (request.sample_kind == SampleKind::Real &&
      !HasText(request.window.title) && !HasText(request.window.id)) {
    return "real samples require window title or id metadata";
  }
  if (request.ui_scale.has_value() &&
      (!std::isfinite(*request.ui_scale) || *request.ui_scale <= 0.0)) {
    return "ui_scale must be finite and positive when present";
  }
  if (!std::isfinite(request.detector.score) || request.detector.score < 0.0F ||
      request.detector.score > 1.0F || request.detector.reason.empty()) {
    return "detector metadata is invalid";
  }
  if (!request.rois.offer.IsInside(frame.width, frame.height)) {
    return "offer ROI is outside the raw frame";
  }
  for (std::size_t index = 0U; index < request.rois.cards.size(); ++index) {
    const auto& rois = request.rois.cards[index];
    if (!rois.card.IsInside(frame.width, frame.height) ||
        !Contains(request.rois.offer, rois.card)) {
      return "card ROI is outside the frame or offer ROI";
    }
    if (rois.title.has_value() &&
        (!rois.title->IsInside(frame.width, frame.height) ||
         !Contains(rois.card, *rois.title))) {
      return "title ROI is outside its card ROI";
    }
    if (rois.icon.has_value() &&
        (!rois.icon->IsInside(frame.width, frame.height) ||
         !Contains(rois.card, *rois.icon))) {
      return "icon ROI is outside its card ROI";
    }
    const auto& ocr = request.cards[index];
    if (!IsKnown(ocr.status)) {
      return "OCR status is invalid";
    }
    if (ocr.confidence.has_value() &&
        (!std::isfinite(*ocr.confidence) || *ocr.confidence < 0.0F ||
         *ocr.confidence > 1.0F)) {
      return "OCR confidence must be inside [0,1] when present";
    }
    if (ocr.matched_id.has_value() && ocr.matched_id->empty()) {
      return "matched OCR id cannot be empty";
    }
    if ((ocr.status == OcrSampleStatus::Matched) !=
        ocr.matched_id.has_value()) {
      return "matched OCR status and matched id must agree";
    }
  }
  return std::nullopt;
}

[[nodiscard]] std::uint64_t ExactContentHash(
    const common::Frame& frame) noexcept {
  constexpr std::uint64_t offset_basis = 14695981039346656037ULL;
  constexpr std::uint64_t prime = 1099511628211ULL;
  std::uint64_t hash = offset_basis;
  for (const std::uint8_t value : frame.buffer) {
    hash ^= value;
    hash *= prime;
  }
  return hash;
}

[[nodiscard]] std::string Hex64(const std::uint64_t value) {
  constexpr char digits[] = "0123456789abcdef";
  std::string output(16U, '0');
  for (std::size_t index = 0U; index < output.size(); ++index) {
    const auto shift = (output.size() - 1U - index) * 4U;
    output[index] = digits[(value >> shift) & 0x0FU];
  }
  return output;
}

[[nodiscard]] std::optional<std::uint64_t> DifferenceHash(
    const common::Frame& frame) {
  const detector::PixelRoi full_frame{0U, 0U, frame.width, frame.height};
  auto crop = detector::CropBgraOwning(frame, full_frame);
  if (!crop.ok()) {
    return std::nullopt;
  }
  return vision::ComputeDifferenceHash(*crop.value).hash;
}

[[nodiscard]] common::Frame CropAsFrame(
    const common::Frame& raw, const detector::PixelRoi& roi) {
  auto crop = detector::CropBgraOwning(raw, roi);
  if (!crop.ok()) {
    throw std::invalid_argument("unable to crop card ROI: " + crop.reason);
  }
  common::Frame output;
  output.source = raw.source;
  output.frame_id = raw.frame_id;
  output.timestamps = raw.timestamps;
  output.width = crop.value->width;
  output.height = crop.value->height;
  output.stride = crop.value->stride;
  output.buffer = std::move(crop.value->pixels);
  if (!output.IsValid()) {
    throw std::runtime_error("card crop did not produce a valid frame");
  }
  return output;
}

[[nodiscard]] std::string_view SampleKindName(const SampleKind kind) noexcept {
  switch (kind) {
    case SampleKind::Real:
      return "real";
    case SampleKind::Synthetic:
      return "synthetic";
    case SampleKind::Unknown:
      return "unknown";
  }
  return "unknown";
}

[[nodiscard]] std::string_view CaptureReasonName(
    const CaptureReason reason) noexcept {
  switch (reason) {
    case CaptureReason::DetectorSuspect:
      return "detector_suspect";
    case CaptureReason::ManualF8:
      return "manual_f8";
  }
  return "detector_suspect";
}

[[nodiscard]] std::string_view OcrStatusName(
    const OcrSampleStatus status) noexcept {
  switch (status) {
    case OcrSampleStatus::NotRun:
      return "not_run";
    case OcrSampleStatus::BackendUnavailable:
      return "backend_unavailable";
    case OcrSampleStatus::Failed:
      return "failed";
    case OcrSampleStatus::Unknown:
      return "unknown";
    case OcrSampleStatus::Matched:
      return "matched";
  }
  return "unknown";
}

[[nodiscard]] std::string_view FrameSourceName(
    const common::FrameSourceKind kind) noexcept {
  switch (kind) {
    case common::FrameSourceKind::Unknown:
      return "unknown";
    case common::FrameSourceKind::WindowsGraphicsCapture:
      return "windows_graphics_capture";
    case common::FrameSourceKind::DesktopDuplication:
      return "desktop_duplication";
    case common::FrameSourceKind::Replay:
      return "replay";
    case common::FrameSourceKind::Stub:
      return "stub";
  }
  return "unknown";
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

void AppendOptionalDouble(std::string& json,
                          const std::optional<double>& value) {
  if (value.has_value()) {
    output::detail::AppendFloating(json, *value);
  } else {
    json.append("null");
  }
}

void AppendOptionalUnsigned(std::string& json,
                            const std::optional<std::uint32_t>& value) {
  if (value.has_value()) {
    json.append(std::to_string(*value));
  } else {
    json.append("null");
  }
}

void AppendRoi(std::string& json, const detector::PixelRoi& roi) {
  json.append("{\"x\":");
  json.append(std::to_string(roi.x));
  json.append(",\"y\":");
  json.append(std::to_string(roi.y));
  json.append(",\"width\":");
  json.append(std::to_string(roi.width));
  json.append(",\"height\":");
  json.append(std::to_string(roi.height));
  json.push_back('}');
}

void AppendOptionalRoi(std::string& json,
                       const std::optional<detector::PixelRoi>& roi) {
  if (roi.has_value()) {
    AppendRoi(json, *roi);
  } else {
    json.append("null");
  }
}

[[nodiscard]] std::string BuildMetadataJson(
    const common::Frame& frame, const SampleCollectionRequest& request,
    const SampleCollectorConfig& config, const std::string_view sample_id,
    const common::UtcTimestamp timestamp, const std::uint64_t exact_hash,
    const std::uint64_t difference_hash) {
  constexpr std::array<std::string_view, common::kAugmentCardCount> slots{
      "left", "center", "right"};
  std::string json;
  json.reserve(4096U);
  json.append("{\"schema\":");
  output::detail::AppendQuotedUtf8(json, kSampleMetadataSchema);
  json.append(",\"version\":");
  json.append(std::to_string(kSampleMetadataVersion));
  json.append(",\"schema_version\":");
  json.append(std::to_string(kDatasetMetadataSchemaVersion));
  json.append(",\"sample_id\":");
  output::detail::AppendQuotedUtf8(json, sample_id);
  json.append(",\"sample_kind\":");
  output::detail::AppendQuotedUtf8(json, SampleKindName(request.sample_kind));
  json.append(",\"provenance\":");
  output::detail::AppendQuotedUtf8(json, SampleKindName(request.sample_kind));
  json.append(",\"timestamp\":");
  output::detail::AppendQuotedUtf8(
      json, output::detail::FormatUtcTimestamp(timestamp));
  json.append(",\"captured_at_utc\":");
  output::detail::AppendQuotedUtf8(
      json, output::detail::FormatUtcTimestamp(timestamp));
  json.append(",\"source\":{\"kind\":");
  output::detail::AppendQuotedUtf8(json, FrameSourceName(frame.source.kind));
  json.append(",\"id\":");
  output::detail::AppendQuotedUtf8(json, frame.source.id);
  json.append(",\"reference\":");
  output::detail::AppendQuotedUtf8(json, frame.source.id);
  json.append("},\"window\":{\"title\":");
  AppendOptionalString(json, request.window.title);
  json.append(",\"id\":");
  AppendOptionalString(json, request.window.id);
  json.append(",\"process_id\":");
  AppendOptionalUnsigned(json, request.window.process_id);
  json.append("},\"resolution\":{\"width\":");
  json.append(std::to_string(frame.width));
  json.append(",\"height\":");
  json.append(std::to_string(frame.height));
  json.append(",\"stride\":");
  json.append(std::to_string(frame.stride));
  json.append("},\"ui_scale\":");
  AppendOptionalDouble(json, request.ui_scale);
  json.append(",\"rois\":{\"coordinate_space\":");
  output::detail::AppendQuotedUtf8(json, "raw_frame_pixels");
  json.append(",\"offer\":");
  AppendRoi(json, request.rois.offer);
  json.append(",\"cards\":[");
  for (std::size_t index = 0U; index < request.rois.cards.size(); ++index) {
    if (index != 0U) {
      json.push_back(',');
    }
    json.append("{\"slot\":");
    output::detail::AppendQuotedUtf8(json, slots[index]);
    json.append(",\"card\":");
    AppendRoi(json, request.rois.cards[index].card);
    json.append(",\"title\":");
    AppendOptionalRoi(json, request.rois.cards[index].title);
    json.append(",\"icon\":");
    AppendOptionalRoi(json, request.rois.cards[index].icon);
    json.push_back('}');
  }
  json.append("]},\"detector\":{\"visible\":");
  json.append(request.detector.visible ? "true" : "false");
  json.append(",\"score\":");
  output::detail::AppendFloating(json, request.detector.score);
  json.append(",\"reason\":");
  output::detail::AppendQuotedUtf8(json, request.detector.reason);
  json.append("},\"cards\":[");
  for (std::size_t index = 0U; index < request.cards.size(); ++index) {
    if (index != 0U) {
      json.push_back(',');
    }
    const auto& card = request.cards[index];
    json.append("{\"slot\":");
    output::detail::AppendQuotedUtf8(json, slots[index]);
    json.append(",\"ocr\":{\"raw\":");
    output::detail::AppendQuotedUtf8(json, card.raw);
    json.append(",\"lines\":[");
    for (std::size_t line = 0U; line < card.lines.size(); ++line) {
      if (line != 0U) {
        json.push_back(',');
      }
      output::detail::AppendQuotedUtf8(json, card.lines[line]);
    }
    json.append("],\"matched_id\":");
    AppendOptionalString(json, card.matched_id);
    json.append(",\"status\":");
    output::detail::AppendQuotedUtf8(json, OcrStatusName(card.status));
    json.append(",\"confidence\":");
    AppendOptionalFloat(json, card.confidence);
    json.append("}}");
  }
  json.append("],\"capture_reason\":");
  output::detail::AppendQuotedUtf8(
      json, CaptureReasonName(request.capture_reason));
  json.append(",\"capture_reason_detail\":");
  AppendOptionalString(json, request.capture_reason_detail);
  json.append(",\"dedup\":{\"exact_hash_algorithm\":");
  output::detail::AppendQuotedUtf8(json, "fnv1a64_bgra8_buffer");
  json.append(",\"exact_hash\":");
  output::detail::AppendQuotedUtf8(json, Hex64(exact_hash));
  json.append(",\"dhash_algorithm\":");
  output::detail::AppendQuotedUtf8(json, "difference_hash_9x8_luma64");
  json.append(",\"dhash\":");
  output::detail::AppendQuotedUtf8(json, Hex64(difference_hash));
  json.append(",\"maximum_dhash_distance\":");
  json.append(std::to_string(config.dedup.maximum_dhash_distance));
  json.append(",\"minimum_interval_ms\":");
  json.append(std::to_string(config.dedup.minimum_interval.count()));
  json.append(",\"manual_bypasses_minimum_interval\":");
  json.append(config.dedup.manual_bypasses_minimum_interval ? "true"
                                                            : "false");
  json.append(",\"manual_allows_exact_duplicate\":");
  json.append(config.dedup.manual_allows_exact_duplicate ? "true" : "false");
  json.append("},\"files\":{\"raw\":\"RAW.png\","
              "\"left_card\":\"LEFT_CARD.png\","
              "\"center_card\":\"CENTER_CARD.png\","
              "\"right_card\":\"RIGHT_CARD.png\","
              "\"metadata\":\"metadata.json\"}}");
  return json;
}

void WriteNewTextFile(const std::filesystem::path& path,
                      const std::string_view text) {
  if (std::filesystem::exists(path)) {
    throw std::runtime_error("refusing to overwrite metadata file: " +
                             path.string());
  }
  std::ofstream output(path, std::ios::binary | std::ios::out);
  if (!output) {
    throw std::runtime_error("unable to create metadata file: " +
                             path.string());
  }
  output.write(text.data(), static_cast<std::streamsize>(text.size()));
  output.flush();
  if (!output) {
    throw std::runtime_error("unable to write complete metadata file: " +
                             path.string());
  }
  output.close();
  if (!output) {
    throw std::runtime_error("unable to close metadata file: " +
                             path.string());
  }
}

[[nodiscard]] std::string ReadTextFile(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw std::runtime_error("unable to reopen metadata file: " +
                             path.string());
  }
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

void VerifyStagedSample(const std::filesystem::path& staging,
                        const std::string_view expected_metadata) {
  std::size_t entry_count = 0U;
  for (const auto& entry : std::filesystem::directory_iterator(staging)) {
    ++entry_count;
    const auto name = entry.path().filename().wstring();
    const bool expected = std::ranges::any_of(
        kRequiredFiles,
        [&name](const std::wstring_view value) { return name == value; });
    if (!expected || !entry.is_regular_file() || entry.file_size() == 0U) {
      throw std::runtime_error("staged sample contains an invalid file");
    }
  }
  if (entry_count != kRequiredFiles.size()) {
    throw std::runtime_error("staged sample does not contain exactly five files");
  }
  for (const auto filename : kRequiredFiles) {
    const auto path = staging / filename;
    if (!std::filesystem::is_regular_file(path) ||
        std::filesystem::file_size(path) == 0U) {
      throw std::runtime_error("staged sample is missing a required file");
    }
  }
  const std::string metadata = ReadTextFile(staging / L"metadata.json");
  if (metadata != expected_metadata || !IsMetadataJsonParseable(metadata)) {
    throw SerializationFailure(
        "metadata.json was incomplete or failed strict JSON parsing");
  }
}

[[nodiscard]] std::string MakeSampleId(
    const common::UtcTimestamp timestamp, const std::uint64_t frame_id,
    const std::uint64_t exact_hash, const std::uint64_t sequence) {
  const auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
                          timestamp.time_since_epoch())
                          .count();
  return "sample_" + std::to_string(micros) + "_f" +
         std::to_string(frame_id) + "_p" +
         std::to_string(GetCurrentProcessId()) + "_n" +
         std::to_string(sequence) + "_" + Hex64(exact_hash);
}

[[nodiscard]] CollectResult FailureResult(
    const CollectStatus status, const std::string& message,
    const std::filesystem::path& dataset_root,
    const std::filesystem::path& staging, const bool staging_created) noexcept {
  std::string full_message = message;
  if (staging_created) {
    try {
      RequireDescendantPath(dataset_root, staging);
      std::error_code cleanup_error;
      // Roll back only the unique temporary directory created by this call.
      // Existing/committed samples are never removed or overwritten.
      std::filesystem::remove_all(staging, cleanup_error);
      if (cleanup_error) {
        full_message.append("; rollback_failed: ");
        full_message.append(cleanup_error.message());
      }
    } catch (const std::exception& error) {
      full_message.append("; rollback_guard_failed: ");
      full_message.append(error.what());
    }
  }
  return {status, {}, std::move(full_message)};
}

}  // namespace

bool IsMetadataJsonParseable(const std::string_view text) noexcept {
  return JsonSyntaxValidator{text}.Parse();
}

struct SampleCollector::Impl final {
  struct LastSaved final {
    std::vector<std::uint8_t> raw_bytes{};
    std::uint64_t difference_hash{0U};
    common::MonotonicTimestamp observed_at{};
    std::filesystem::path sample_directory{};
  };

  SampleCollectorConfig config{};
  std::shared_ptr<const ISampleImageWriter> image_writer{};
  std::mutex mutex{};
  std::optional<LastSaved> last_saved{};
  std::optional<SampleKind> dataset_bucket_kind{};
  std::uint64_t sequence{0U};
};

SampleCollector::SampleCollector(
    SampleCollectorConfig config,
    std::shared_ptr<const ISampleImageWriter> image_writer)
    : impl_(std::make_unique<Impl>()) {
  if (config.dataset_root.empty() || !config.dataset_root.is_absolute() ||
      HasDotDotComponent(config.dataset_root)) {
    throw std::invalid_argument(
        "dataset_root must be absolute and contain no '..' component");
  }
  if (config.dedup.minimum_interval.count() < 0 ||
      config.dedup.maximum_dhash_distance > 64U) {
    throw std::invalid_argument("sample dedup policy is invalid");
  }

  std::error_code error;
  std::filesystem::create_directories(config.dataset_root, error);
  if (error || !std::filesystem::is_directory(config.dataset_root)) {
    throw std::runtime_error("unable to create dataset_root directory");
  }
  config.dataset_root = std::filesystem::weakly_canonical(config.dataset_root);
  if (!config.dataset_root.is_absolute()) {
    throw std::runtime_error("dataset_root did not resolve to an absolute path");
  }
  impl_->dataset_bucket_kind = DatasetBucketKind(config.dataset_root);
  impl_->config = std::move(config);
  impl_->image_writer = image_writer != nullptr
                            ? std::move(image_writer)
                            : std::make_shared<WicSampleImageWriter>();
}

SampleCollector::~SampleCollector() = default;

CollectResult SampleCollector::Collect(
    const common::Frame& raw_frame,
    const SampleCollectionRequest& request) noexcept {
  try {
    if (const auto invalid = ValidateRequest(raw_frame, request);
        invalid.has_value()) {
      return {CollectStatus::InvalidRequest, {}, *invalid};
    }
    if (impl_->dataset_bucket_kind.has_value() &&
        request.sample_kind != *impl_->dataset_bucket_kind) {
      return {CollectStatus::InvalidRequest, {},
              "sample provenance does not match dataset_root bucket"};
    }

    const auto difference_hash = DifferenceHash(raw_frame);
    if (!difference_hash.has_value()) {
      return {CollectStatus::InvalidRequest, {},
              "unable to compute raw-frame difference hash"};
    }
    const std::uint64_t exact_hash = ExactContentHash(raw_frame);
    const auto observed_at = request.dedup_observed_at.value_or(
        std::chrono::steady_clock::now());
    const auto timestamp =
        request.timestamp.has_value()
            ? *request.timestamp
            : raw_frame.timestamps.captured_at_utc.value_or(
                  std::chrono::system_clock::now());

    std::lock_guard lock(impl_->mutex);
    if (impl_->last_saved.has_value()) {
      const bool manual = request.capture_reason == CaptureReason::ManualF8;
      const bool exact_duplicate =
          impl_->last_saved->raw_bytes == raw_frame.buffer;
      if (exact_duplicate &&
          !(manual && impl_->config.dedup.manual_allows_exact_duplicate)) {
        return {CollectStatus::Duplicate,
                impl_->last_saved->sample_directory,
                "duplicate_exact_raw_bytes"};
      }

      const auto elapsed = observed_at >= impl_->last_saved->observed_at
                               ? observed_at - impl_->last_saved->observed_at
                               : common::MonotonicTimestamp::duration::zero();
      const bool inside_interval =
          elapsed < impl_->config.dedup.minimum_interval;
      const bool manual_bypass =
          manual && impl_->config.dedup.manual_bypasses_minimum_interval;
      const auto distance = static_cast<std::uint32_t>(std::popcount(
          *difference_hash ^ impl_->last_saved->difference_hash));
      if (!manual_bypass && inside_interval &&
          distance <= impl_->config.dedup.maximum_dhash_distance) {
        return {CollectStatus::Duplicate,
                impl_->last_saved->sample_directory,
                "duplicate_similar_dhash_inside_minimum_interval"};
      }
    }

    const std::string sample_id =
        MakeSampleId(timestamp, raw_frame.frame_id, exact_hash,
                     impl_->sequence++);
    const std::filesystem::path final_directory =
        impl_->config.dataset_root / sample_id;
    const std::filesystem::path staging_directory =
        impl_->config.dataset_root /
        std::filesystem::path(L".tmp_" +
                              std::wstring(sample_id.begin(), sample_id.end()));
    RequireDescendantPath(impl_->config.dataset_root, final_directory);
    RequireDescendantPath(impl_->config.dataset_root, staging_directory);
    if (std::filesystem::exists(final_directory) ||
        std::filesystem::exists(staging_directory)) {
      return {CollectStatus::IoError, {},
              "generated sample directory already exists; refusing overwrite"};
    }

    bool staging_created = false;
    try {
      if (!std::filesystem::create_directory(staging_directory)) {
        return {CollectStatus::IoError, {},
                "unable to create unique staging directory"};
      }
      staging_created = true;

      for (const auto filename : kRequiredFiles) {
        RequireDescendantPath(impl_->config.dataset_root,
                              staging_directory / filename);
      }
      impl_->image_writer->SavePng(raw_frame,
                                   staging_directory / L"RAW.png");
      const std::array<std::wstring_view, common::kAugmentCardCount>
          card_filenames{L"LEFT_CARD.png", L"CENTER_CARD.png",
                         L"RIGHT_CARD.png"};
      for (std::size_t index = 0U; index < card_filenames.size(); ++index) {
        const common::Frame card =
            CropAsFrame(raw_frame, request.rois.cards[index].card);
        impl_->image_writer->SavePng(card,
                                    staging_directory / card_filenames[index]);
      }

      const std::string metadata = BuildMetadataJson(
          raw_frame, request, impl_->config, sample_id, timestamp, exact_hash,
          *difference_hash);
      if (!IsMetadataJsonParseable(metadata)) {
        throw SerializationFailure(
            "generated metadata failed strict JSON parsing");
      }
      WriteNewTextFile(staging_directory / L"metadata.json", metadata);
      VerifyStagedSample(staging_directory, metadata);

      std::error_code rename_error;
      std::filesystem::rename(staging_directory, final_directory, rename_error);
      if (rename_error) {
        throw std::runtime_error("atomic sample directory rename failed: " +
                                 rename_error.message());
      }
      staging_created = false;
      impl_->last_saved = Impl::LastSaved{raw_frame.buffer, *difference_hash,
                                          observed_at, final_directory};
      return {CollectStatus::Saved, final_directory, "saved"};
    } catch (const SerializationFailure& error) {
      return FailureResult(CollectStatus::SerializationError, error.what(),
                           impl_->config.dataset_root, staging_directory,
                           staging_created);
    } catch (const std::exception& error) {
      return FailureResult(CollectStatus::IoError, error.what(),
                           impl_->config.dataset_root, staging_directory,
                           staging_created);
    } catch (...) {
      return FailureResult(CollectStatus::IoError,
                           "unknown sample collection failure",
                           impl_->config.dataset_root, staging_directory,
                           staging_created);
    }
  } catch (const std::exception& error) {
    return {CollectStatus::InvalidRequest, {}, error.what()};
  } catch (...) {
    return {CollectStatus::InvalidRequest, {},
            "unknown sample validation failure"};
  }
}

const std::filesystem::path& SampleCollector::dataset_root() const noexcept {
  return impl_->config.dataset_root;
}

}  // namespace lol_assistant::collection
