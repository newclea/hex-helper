#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/detector/augment_screen_detector.h"
#include "lol_assistant/detector/roi.h"
#include "lol_assistant/knowledge/augment_catalog.h"
#include "lol_assistant/replay/wic_image_codec.h"
#include "lol_assistant/vision/icon_matcher.h"
#include "lol_assistant/vision/ocr.h"
#include "lol_assistant/vision/text_matcher.h"
#include "lol_assistant/vision/title_preprocessor.h"

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using lol_assistant::common::Frame;
using lol_assistant::common::FrameSource;
using lol_assistant::common::FrameSourceKind;
using lol_assistant::detector::OwningBgraCrop;
using lol_assistant::detector::PixelRect;
using lol_assistant::vision::IconMatchResult;
using lol_assistant::vision::TextMatchKind;
using lol_assistant::vision::TextMatchResult;

namespace {

constexpr PixelRect kPaintedPreview{0U, 0U, 1280U, 720U};
constexpr PixelRect kAspectEnvelope{0U, 0U, 1280U, 800U};
constexpr std::array<PixelRect, 3U> kCards{{
    {253U, 128U, 238U, 353U},
    {526U, 128U, 238U, 353U},
    {799U, 128U, 238U, 353U},
}};
constexpr std::array<PixelRect, 3U> kTitles{{
    {283U, 284U, 178U, 23U},
    {556U, 284U, 178U, 23U},
    {829U, 284U, 178U, 23U},
}};
constexpr std::array<PixelRect, 3U> kIcons{{
    {298U, 150U, 148U, 120U},
    {571U, 150U, 148U, 120U},
    {844U, 150U, 148U, 120U},
}};
constexpr std::array<std::string_view, 3U> kSlots{"LEFT", "CENTER", "RIGHT"};

struct Offer final {
  std::string id;
  fs::path path;
  std::array<std::string, 3U> truth;
};

struct MatchSummary final {
  TextMatchResult best{};
  bool matched{false};
  bool conflicting_ids{false};
};

struct OcrObservation final {
  std::string card_id;
  std::string truth;
  std::optional<std::string> prediction;
  TextMatchKind kind{TextMatchKind::Unknown};
  std::string reason;
  bool conflicting_ids{false};
  double latency_ms{0.0};
  TextMatchResult match{};
};

struct VariantRun final {
  std::string id;
  std::vector<OcrObservation> observations;
};

[[nodiscard]] std::string ReadAll(const fs::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw std::runtime_error("cannot open " + path.string());
  }
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

[[nodiscard]] Frame CropFrame(const Frame& source, const PixelRect rect,
                              const std::uint64_t id) {
  if (!rect.IsInside(source.width, source.height)) {
    throw std::runtime_error("crop out of bounds");
  }
  Frame result;
  result.source = {FrameSourceKind::Replay, "preview-derived-probe"};
  result.frame_id = id;
  result.width = rect.width;
  result.height = rect.height;
  result.stride = rect.width * 4U;
  result.buffer.resize(static_cast<std::size_t>(result.stride) * result.height);
  for (std::uint32_t y = 0U; y < rect.height; ++y) {
    const auto* begin = source.buffer.data() +
                        static_cast<std::size_t>(rect.y + y) * source.stride +
                        static_cast<std::size_t>(rect.x) * 4U;
    std::copy_n(begin, result.stride,
                result.buffer.data() + static_cast<std::size_t>(y) * result.stride);
  }
  if (!result.IsValid()) {
    throw std::runtime_error("invalid crop");
  }
  return result;
}

[[nodiscard]] OwningBgraCrop Crop(const Frame& frame, const PixelRect rect) {
  lol_assistant::detector::PixelRoi roi{rect.x, rect.y, rect.width, rect.height};
  auto result = lol_assistant::detector::CropRawBgraOwning(frame, roi);
  if (!result.ok()) {
    throw std::runtime_error("crop failed: " + result.reason);
  }
  return std::move(*result.value);
}

[[nodiscard]] Frame CropAsFrame(const Frame& frame, const PixelRect rect,
                                const std::uint64_t id) {
  return CropFrame(frame, rect, id);
}

void SaveIfMissing(const Frame& frame, const fs::path& path) {
  if (!fs::exists(path)) {
    lol_assistant::replay::WicImageCodec::SavePng(frame, path);
  }
}

[[nodiscard]] double Milliseconds(const Clock::time_point begin,
                                  const Clock::time_point end) {
  return std::chrono::duration<double, std::milli>(end - begin).count();
}

[[nodiscard]] double Average(const std::vector<double>& values) {
  double sum = 0.0;
  for (const double value : values) {
    sum += value;
  }
  return values.empty() ? 0.0 : sum / static_cast<double>(values.size());
}

[[nodiscard]] double P95(std::vector<double> values) {
  if (values.empty()) {
    return 0.0;
  }
  std::sort(values.begin(), values.end());
  const auto rank = static_cast<std::size_t>(
      std::ceil(0.95 * static_cast<double>(values.size())));
  return values[std::max<std::size_t>(1U, rank) - 1U];
}

[[nodiscard]] double Iou(const PixelRect left, const PixelRect right) {
  const std::uint32_t x0 = std::max(left.x, right.x);
  const std::uint32_t y0 = std::max(left.y, right.y);
  const std::uint32_t x1 = std::min(left.x + left.width, right.x + right.width);
  const std::uint32_t y1 = std::min(left.y + left.height, right.y + right.height);
  const std::uint64_t intersection =
      x1 > x0 && y1 > y0
          ? static_cast<std::uint64_t>(x1 - x0) * (y1 - y0)
          : 0U;
  const std::uint64_t area_left =
      static_cast<std::uint64_t>(left.width) * left.height;
  const std::uint64_t area_right =
      static_cast<std::uint64_t>(right.width) * right.height;
  return static_cast<double>(intersection) /
         static_cast<double>(area_left + area_right - intersection);
}

[[nodiscard]] std::string KindName(const TextMatchKind kind) {
  switch (kind) {
    case TextMatchKind::Exact: return "exact";
    case TextMatchKind::Normalized: return "normalized";
    case TextMatchKind::Fuzzy: return "fuzzy";
    case TextMatchKind::Unknown: return "unknown";
  }
  return "unknown";
}

[[nodiscard]] int KindRank(const TextMatchKind kind) {
  switch (kind) {
    case TextMatchKind::Exact: return 3;
    case TextMatchKind::Normalized: return 2;
    case TextMatchKind::Fuzzy: return 1;
    case TextMatchKind::Unknown: return 0;
  }
  return 0;
}

[[nodiscard]] bool Better(const TextMatchResult& candidate,
                          const TextMatchResult& current) {
  if (KindRank(candidate.kind) != KindRank(current.kind)) {
    return KindRank(candidate.kind) > KindRank(current.kind);
  }
  if (candidate.top1_score != current.top1_score) {
    return candidate.top1_score > current.top1_score;
  }
  return candidate.margin > current.margin;
}

[[nodiscard]] MatchSummary MatchOcr(
    const lol_assistant::vision::OcrTextResult& ocr,
    const lol_assistant::vision::BoundedTextMatcher& matcher,
    const std::vector<lol_assistant::vision::TitleCandidate>& candidates) {
  std::vector<std::string> inputs;
  const auto append = [&inputs](const std::string& value) {
    if (!value.empty() &&
        std::find(inputs.begin(), inputs.end(), value) == inputs.end()) {
      inputs.push_back(value);
    }
  };
  append(ocr.raw_text);
  const auto count = std::min<std::size_t>(
      ocr.line_candidates.size(), lol_assistant::vision::kMaximumOcrLineCandidates);
  for (std::size_t i = 0U; i < count; ++i) {
    append(ocr.line_candidates[i]);
  }
  for (std::size_t i = 0U; i + 1U < count; ++i) {
    append(ocr.line_candidates[i] + " " + ocr.line_candidates[i + 1U]);
  }

  MatchSummary summary;
  bool diagnostic = false;
  for (const auto& input : inputs) {
    auto result = matcher.Match(input, candidates);
    if (!result.matched()) {
      if (!summary.matched && (!diagnostic ||
          result.top1_score > summary.best.top1_score)) {
        summary.best = std::move(result);
        diagnostic = true;
      }
      continue;
    }
    if (summary.matched && result.id != summary.best.id) {
      summary.conflicting_ids = true;
      if (Better(result, summary.best)) {
        summary.best = std::move(result);
      }
      continue;
    }
    if (!summary.matched || Better(result, summary.best)) {
      summary.best = std::move(result);
    }
    summary.matched = true;
  }
  if (!summary.matched && !diagnostic) {
    summary.best.reason = "no_bounded_ocr_text_candidate";
  }
  return summary;
}

[[nodiscard]] OwningBgraCrop GrayToBgra(
    const lol_assistant::vision::OwningGrayImage& image) {
  OwningBgraCrop crop;
  crop.width = image.width;
  crop.height = image.height;
  crop.stride = image.width * 4U;
  crop.pixels.resize(static_cast<std::size_t>(crop.stride) * crop.height);
  for (std::uint32_t y = 0U; y < crop.height; ++y) {
    for (std::uint32_t x = 0U; x < crop.width; ++x) {
      const auto gray = image.pixels[static_cast<std::size_t>(y) * image.stride + x];
      const auto offset = static_cast<std::size_t>(y) * crop.stride + x * 4U;
      crop.pixels[offset + 0U] = gray;
      crop.pixels[offset + 1U] = gray;
      crop.pixels[offset + 2U] = gray;
      crop.pixels[offset + 3U] = 255U;
    }
  }
  return crop;
}

[[nodiscard]] std::vector<lol_assistant::vision::TitlePreprocessParameters>
Parameters() {
  using lol_assistant::vision::TitlePreprocessParameters;
  using lol_assistant::vision::TitleThresholdMode;
  std::vector<TitlePreprocessParameters> result;
  for (const bool contrast : {false, true}) {
    for (const auto threshold : {TitleThresholdMode::None,
                                 TitleThresholdMode::Fixed,
                                 TitleThresholdMode::Otsu}) {
      for (std::uint32_t scale = 1U; scale <= 3U; ++scale) {
        TitlePreprocessParameters value;
        value.contrast_stretch = contrast;
        value.threshold_mode = threshold;
        value.fixed_threshold = 128U;
        value.scale = scale;
        result.push_back(value);
      }
    }
  }
  return result;
}

[[nodiscard]] std::string ExtractString(const std::string_view object,
                                        const std::string_view key) {
  const std::string marker = "\"" + std::string(key) + "\": \"";
  const auto begin = object.find(marker);
  if (begin == std::string_view::npos) {
    return {};
  }
  const auto value_begin = begin + marker.size();
  const auto end = object.find('"', value_begin);
  return end == std::string_view::npos
             ? std::string{}
             : std::string{object.substr(value_begin, end - value_begin)};
}

[[nodiscard]] std::vector<std::string> ExtractModes(
    const std::string_view object) {
  std::vector<std::string> modes;
  const auto marker = object.find("\"modes\": [");
  if (marker == std::string_view::npos) {
    return modes;
  }
  const auto end = object.find(']', marker);
  if (end == std::string_view::npos) {
    return modes;
  }
  auto position = marker;
  while ((position = object.find('"', position)) != std::string_view::npos &&
         position < end) {
    const auto close = object.find('"', position + 1U);
    if (close == std::string_view::npos || close > end) {
      break;
    }
    const auto value = object.substr(position + 1U, close - position - 1U);
    if (value != "modes") {
      modes.emplace_back(value);
    }
    position = close + 1U;
  }
  return modes;
}

[[nodiscard]] std::vector<lol_assistant::vision::IconHashTemplate>
LoadIconTemplates(const fs::path& path) {
  const std::string input = ReadAll(path);
  std::vector<lol_assistant::vision::IconHashTemplate> templates;
  std::size_t position = 0U;
  while ((position = input.find("\"augment_id\": \"", position)) !=
         std::string::npos) {
    const auto next = input.find("\"augment_id\": \"", position + 1U);
    const auto object_end = next == std::string::npos ? input.size() : next;
    const std::string_view object{input.data() + position, object_end - position};
    const auto id = ExtractString(object, "augment_id");
    const auto hash_text = ExtractString(object, "difference_hash");
    if (!id.empty() && hash_text.size() == 16U) {
      std::uint64_t hash = 0U;
      std::istringstream stream(hash_text);
      stream >> std::hex >> hash;
      if (!stream.fail()) {
        templates.push_back({id, hash, ExtractModes(object)});
      }
    }
    position = object_end;
  }
  if (templates.empty()) {
    throw std::runtime_error("no icon templates parsed");
  }
  return templates;
}

[[nodiscard]] lol_assistant::detector::AugmentScreenDetectorConfig
DetectorConfig() {
  using lol_assistant::common::NormalizedRoi;
  lol_assistant::detector::AugmentScreenDetectorConfig config;
  config.layout.offer_region = {0.10, 0.10, 0.80, 0.80};
  config.layout.card_regions = {
      NormalizedRoi{0.15, 0.20, 0.20, 0.55},
      NormalizedRoi{0.40, 0.20, 0.20, 0.55},
      NormalizedRoi{0.65, 0.20, 0.20, 0.55},
  };
  return config;
}

[[nodiscard]] std::string Join(const std::vector<std::string>& values) {
  std::string output;
  for (std::size_t i = 0U; i < values.size(); ++i) {
    if (i != 0U) output.push_back(',');
    output.append(values[i]);
  }
  return output.empty() ? "-" : output;
}

}  // namespace

int wmain(int argc, wchar_t* argv[]) {
  try {
    const fs::path root = argc > 1 ? fs::absolute(argv[1]) : fs::current_path();
    const fs::path output = root / "outputs/tmp/phase2_real_vision_probe";
    fs::create_directories(output / "crops");

    const std::vector<Offer> offers{
        {"A", root / "outputs/phase2_emergency_real/burst_1787670257978/frame_010.png",
         {"ARAM_Eureka", "OminousPact", "Overloaded"}},
        {"B", root / "outputs/phase2_emergency_real/burst_1787670257978/frame_020.png",
         {"ARAM_Eureka", "OminousPact", "ARAM_EndlessHunt"}},
        {"C", root / "outputs/phase2_emergency_real/continuous_1787670572085/frame_0025.png",
         {"ARAM_CelestialBody", "WarlockJuicebox", "ARAM_WithHaste"}},
    };
    const std::array<std::string_view, 10U> negatives{
        "0015", "0060", "0085", "0090", "0095",
        "0105", "0110", "0115", "0120", "0130"};

    std::vector<Frame> positive_frames;
    for (std::size_t i = 0U; i < offers.size(); ++i) {
      auto source = lol_assistant::replay::WicImageCodec::Decode(
          offers[i].path, FrameSource{FrameSourceKind::Replay, offers[i].id}, i + 1U);
      if (source.width != 1920U || source.height != 1080U) {
        throw std::runtime_error("unexpected preview screenshot dimensions");
      }
      auto game = CropFrame(source, kPaintedPreview, i + 1U);
      SaveIfMissing(game, output / "crops" / (offers[i].id + "_game_1280x720.png"));
      for (std::size_t slot = 0U; slot < 3U; ++slot) {
        SaveIfMissing(CropAsFrame(game, kTitles[slot], 100U + i * 3U + slot),
                      output / "crops" /
                          (offers[i].id + "_" + std::string(kSlots[slot]) + "_title.png"));
        SaveIfMissing(CropAsFrame(game, kIcons[slot], 200U + i * 3U + slot),
                      output / "crops" /
                          (offers[i].id + "_" + std::string(kSlots[slot]) + "_icon.png"));
      }
      positive_frames.push_back(std::move(game));
    }

    std::cout << std::fixed << std::setprecision(4);
    std::cout << "GEOM source=1920x1080 painted=0,0,1280,720 aspect_envelope=0,0,1280,800"
                 " envelope_bottom_80=external_black_not_game_pixels\n";

    lol_assistant::detector::AugmentScreenDetector detector{DetectorConfig()};
    std::size_t tp = 0U, fn = 0U, fp = 0U, tn = 0U;
    std::vector<std::string> detector_failures;
    for (std::size_t i = 0U; i < positive_frames.size(); ++i) {
      const auto result = detector.Detect(positive_frames[i]);
      result.visible ? ++tp : ++fn;
      if (!result.visible) detector_failures.push_back(offers[i].id);
      std::cout << "DET id=" << offers[i].id << " label=positive visible="
                << result.visible << " confidence=" << result.confidence
                << " reason=" << result.reason << " offset="
                << result.metrics.selected_offset_x << ','
                << result.metrics.selected_offset_y << "\n";
      if (result.rois.has_value()) {
        for (std::size_t slot = 0U; slot < 3U; ++slot) {
          const auto card = result.rois->cards[slot].Bounds();
          const auto title = result.rois->title_rects[slot].Bounds();
          const auto icon = result.rois->icon_rects[slot].Bounds();
          const auto emit = [&](const char* kind, PixelRect actual, PixelRect manual) {
            std::cout << "ROI id=" << offers[i].id << " slot=" << kSlots[slot]
                      << " kind=" << kind << " iou=" << Iou(actual, manual)
                      << " delta=" << static_cast<std::int64_t>(actual.x) - manual.x
                      << ',' << static_cast<std::int64_t>(actual.y) - manual.y
                      << ',' << static_cast<std::int64_t>(actual.width) - manual.width
                      << ',' << static_cast<std::int64_t>(actual.height) - manual.height
                      << " predicted=" << actual.x << ',' << actual.y << ','
                      << actual.width << ',' << actual.height << "\n";
          };
          emit("card", card, kCards[slot]);
          emit("title", title, kTitles[slot]);
          emit("icon", icon, kIcons[slot]);
        }
      }
    }
    for (std::size_t i = 0U; i < negatives.size(); ++i) {
      const std::string id = "N" + std::string(negatives[i]);
      const fs::path path = root / "outputs/phase2_emergency_real/continuous_1787670572085" /
                            ("frame_" + std::string(negatives[i]) + ".png");
      auto source = lol_assistant::replay::WicImageCodec::Decode(
          path, FrameSource{FrameSourceKind::Replay, id}, 100U + i);
      auto game = CropFrame(source, kPaintedPreview, 100U + i);
      const auto result = detector.Detect(game);
      result.visible ? ++fp : ++tn;
      if (result.visible) detector_failures.push_back(id);
      std::cout << "DET id=" << id << " label=negative visible="
                << result.visible << " confidence=" << result.confidence
                << " reason=" << result.reason << " offset="
                << result.metrics.selected_offset_x << ','
                << result.metrics.selected_offset_y << "\n";
    }
    std::cout << "DET_SUM tp=" << tp << " fn=" << fn << " fp=" << fp
              << " tn=" << tn << " failures=" << Join(detector_failures) << "\n";

    const auto catalog_result = lol_assistant::knowledge::LoadAugmentCatalog(
        root / "data/knowledge/augments.zh-CN.json");
    if (!catalog_result.ok()) {
      throw std::runtime_error("catalog load failed: " + catalog_result.reason);
    }
    const auto candidates = lol_assistant::vision::BuildTitleCandidates(
        *catalog_result.catalog, "KIWI");
    lol_assistant::vision::BoundedTextMatcher text_matcher;
    lol_assistant::vision::WindowsMediaOcrTitleRecognizer ocr;
    const auto backend = ocr.Probe();
    std::cout << "OCR_BACKEND available=" << backend.available()
              << " reason=" << backend.reason
              << " candidates=" << candidates.size() << "\n";

    std::vector<VariantRun> variant_runs(18U);
    const auto parameters = Parameters();
    for (std::size_t offer_index = 0U; offer_index < offers.size(); ++offer_index) {
      for (std::size_t slot = 0U; slot < 3U; ++slot) {
        const auto title_crop = Crop(positive_frames[offer_index], kTitles[slot]);
        for (std::size_t variant_index = 0U; variant_index < parameters.size();
             ++variant_index) {
          const auto started = Clock::now();
          const auto processed = lol_assistant::vision::TitlePreprocessor::Process(
              title_crop, parameters[variant_index]);
          if (!processed.ok()) {
            throw std::runtime_error("preprocess failed: " + processed.reason);
          }
          variant_runs[variant_index].id = processed.value->id;
          const auto ocr_result = ocr.Recognize(GrayToBgra(processed.value->image));
          MatchSummary match;
          if (ocr_result.ok()) {
            match = MatchOcr(ocr_result, text_matcher, candidates);
          } else {
            match.best.reason = ocr_result.reason;
          }
          const auto finished = Clock::now();
          OcrObservation observation;
          observation.card_id = offers[offer_index].id + "_" + std::string(kSlots[slot]);
          observation.truth = offers[offer_index].truth[slot];
          observation.reason = match.conflicting_ids
                                   ? "conflicting_ocr_candidate_ids"
                                   : match.best.reason;
          observation.conflicting_ids = match.conflicting_ids;
          observation.latency_ms = Milliseconds(started, finished);
          observation.match = match.best;
          if (ocr_result.ok() && match.matched && !match.conflicting_ids) {
            observation.prediction = match.best.id;
            observation.kind = match.best.kind;
          }
          variant_runs[variant_index].observations.push_back(std::move(observation));
        }
      }
      std::cerr << "ocr offer " << offers[offer_index].id << " complete\n";
    }

    for (const auto& run : variant_runs) {
      std::size_t correct = 0U, unknown = 0U, exact = 0U, normalized = 0U,
                  fuzzy = 0U, all_correct = 0U;
      std::vector<double> latencies;
      std::vector<std::string> failures;
      for (std::size_t i = 0U; i < run.observations.size(); ++i) {
        const auto& item = run.observations[i];
        const bool is_correct = item.prediction == item.truth;
        correct += is_correct;
        unknown += !item.prediction.has_value();
        exact += item.kind == TextMatchKind::Exact;
        normalized += item.kind == TextMatchKind::Normalized;
        fuzzy += item.kind == TextMatchKind::Fuzzy;
        latencies.push_back(item.latency_ms);
        if (!is_correct) failures.push_back(item.card_id);
      }
      for (std::size_t offer = 0U; offer < 3U; ++offer) {
        bool okay = true;
        for (std::size_t slot = 0U; slot < 3U; ++slot) {
          const auto& item = run.observations[offer * 3U + slot];
          okay = okay && item.prediction == item.truth;
        }
        all_correct += okay;
      }
      std::cout << "OCR variant=" << run.id << " correct=" << correct
                << "/9 all_correct=" << all_correct << "/3 unknown=" << unknown
                << "/9 exact=" << exact << " normalized=" << normalized
                << " fuzzy=" << fuzzy << " latency_avg_ms=" << Average(latencies)
                << " latency_p95_ms=" << P95(latencies)
                << " failures=" << Join(failures) << " predictions=";
      for (std::size_t i = 0U; i < run.observations.size(); ++i) {
        if (i != 0U) std::cout << ',';
        std::cout << run.observations[i].prediction.value_or("UNKNOWN")
                  << ':' << KindName(run.observations[i].kind);
      }
      std::cout << "\n";
    }

    const auto templates = LoadIconTemplates(
        root / "data/knowledge/augment_icons/manifest.json");
    lol_assistant::vision::PerceptualHashTemplateMatcher icon_matcher{templates};
    std::vector<IconMatchResult> icon_results;
    std::vector<std::string> truths;
    std::vector<std::string> card_ids;
    std::vector<double> icon_latencies;
    std::size_t icon_correct = 0U, icon_unknown = 0U, icon_conflict = 0U;
    std::vector<std::string> icon_failures;
    for (std::size_t offer = 0U; offer < offers.size(); ++offer) {
      for (std::size_t slot = 0U; slot < 3U; ++slot) {
        const auto crop = Crop(positive_frames[offer], kIcons[slot]);
        const auto begin = Clock::now();
        auto result = icon_matcher.Match(crop, "KIWI");
        const auto end = Clock::now();
        const std::string card_id = offers[offer].id + "_" + std::string(kSlots[slot]);
        const bool correct = result.augment_id == offers[offer].truth[slot];
        icon_correct += correct;
        icon_unknown += result.state != lol_assistant::vision::IconMatchState::Matched;
        icon_conflict += result.reason == "hash_top1_ambiguous" ||
                         result.reason == "hash_margin_below_threshold";
        if (!correct) icon_failures.push_back(card_id);
        std::cout << "ICON id=" << card_id << " truth=" << offers[offer].truth[slot]
                  << " prediction=" << result.augment_id.value_or("UNKNOWN")
                  << " reason=" << result.reason << " top1="
                  << result.top1_score.value_or(0.0F) << " margin="
                  << result.margin.value_or(0.0F) << "\n";
        icon_latencies.push_back(Milliseconds(begin, end));
        icon_results.push_back(std::move(result));
        truths.push_back(offers[offer].truth[slot]);
        card_ids.push_back(card_id);
      }
    }
    std::cout << "ICON_SUM templates=" << templates.size() << " correct="
              << icon_correct << "/9 unknown=" << icon_unknown
              << "/9 conflict=" << icon_conflict << "/9 latency_avg_ms="
              << Average(icon_latencies) << " latency_p95_ms=" << P95(icon_latencies)
              << " failures=" << Join(icon_failures) << "\n";

    for (const auto& run : variant_runs) {
      std::size_t correct = 0U, unknown = 0U, all_correct = 0U,
                  conflicts = 0U, conflict_policy_violations = 0U;
      std::vector<std::string> failures;
      std::vector<lol_assistant::vision::FusedAugmentResult> fused;
      for (std::size_t i = 0U; i < run.observations.size(); ++i) {
        TextMatchResult text = run.observations[i].match;
        if (!run.observations[i].prediction.has_value()) {
          text = {};
          text.reason = run.observations[i].reason;
        }
        const auto result = lol_assistant::vision::FuseAugmentIdentity(
            text, icon_results[i]);
        const bool conflict = result.reason.rfind("ocr_icon_conflict:", 0U) == 0U;
        conflicts += conflict;
        if (conflict && result.state != lol_assistant::vision::FusedAugmentState::Unknown) {
          ++conflict_policy_violations;
        }
        const bool is_correct = result.augment_id == truths[i];
        correct += is_correct;
        unknown += result.state == lol_assistant::vision::FusedAugmentState::Unknown;
        if (!is_correct) failures.push_back(card_ids[i]);
        fused.push_back(result);
      }
      for (std::size_t offer = 0U; offer < 3U; ++offer) {
        bool okay = true;
        for (std::size_t slot = 0U; slot < 3U; ++slot) {
          const auto i = offer * 3U + slot;
          okay = okay && fused[i].augment_id == truths[i];
        }
        all_correct += okay;
      }
      std::cout << "FUSE variant=" << run.id << " correct=" << correct
                << "/9 all_correct=" << all_correct << "/3 unknown=" << unknown
                << "/9 ocr_icon_conflicts=" << conflicts
                << " policy_violations=" << conflict_policy_violations
                << " failures=" << Join(failures) << "\n";
    }

    return 0;
  } catch (const std::exception& error) {
    std::cerr << "fatal: " << error.what() << '\n';
    return 1;
  }
}
