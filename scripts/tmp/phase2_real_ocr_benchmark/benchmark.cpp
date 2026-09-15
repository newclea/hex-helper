#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/detector/roi.h"
#include "lol_assistant/knowledge/augment_catalog.h"
#include "lol_assistant/replay/wic_image_codec.h"
#include "lol_assistant/vision/ocr.h"
#include "lol_assistant/vision/text_matcher.h"
#include "lol_assistant/vision/title_preprocessor.h"

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using lol_assistant::detector::OwningBgraCrop;
using lol_assistant::detector::PixelRect;
using lol_assistant::vision::TextMatchKind;
using lol_assistant::vision::TextMatchResult;

namespace {

constexpr std::size_t kMeasuredRepeats = 7U;
constexpr std::string_view kSampleId =
    "sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6";
constexpr std::string_view kSampleRelativePath =
    "data/dataset/augment_offers/real/"
    "sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6/RAW.png";

struct CardSpec final {
  std::string_view slot;
  std::string_view truth_id;
  std::string_view truth_title;
  PixelRect glyph_bbox;
  PixelRect title_roi;
};

constexpr std::array<CardSpec, 3U> kCards{{
    {"left", "ARAM_Impassable", "不动如山", {683U, 645U, 122U, 28U},
     {675U, 638U, 138U, 42U}},
    {"center", "Equilibrium", "我们的治疗", {1212U, 644U, 156U, 29U},
     {1204U, 638U, 172U, 42U}},
    {"right", "ARAM_CelestialBody", "星界躯体", {1774U, 644U, 124U, 29U},
     {1766U, 638U, 140U, 42U}},
}};

struct InputCandidate final {
  std::string value;
  std::string source;
};

struct MatchSummary final {
  TextMatchResult best{};
  std::string selected_input{};
  std::string selected_source{};
  bool matched{false};
  bool conflicting_ids{false};
};

struct Timing final {
  double preprocess_ms{0.0};
  double ocr_ms{0.0};
  double match_ms{0.0};
  double total_ms{0.0};
};

struct Observation final {
  lol_assistant::vision::OcrTextResult ocr{};
  MatchSummary match{};
  std::optional<std::string> prediction{};
  Timing timing{};
  std::optional<lol_assistant::vision::AppliedTitlePreprocessParameters>
      applied{};
};

struct Config final {
  std::string id;
  bool raw{false};
  lol_assistant::vision::TitlePreprocessParameters parameters{};
};

struct CardRun final {
  CardSpec spec{};
  Observation canonical{};
  std::size_t correct_repeats{0U};
  std::size_t exact_repeats{0U};
  std::size_t normalized_repeats{0U};
  std::size_t fuzzy_repeats{0U};
  std::size_t unknown_repeats{0U};
  bool ocr_text_stable{true};
  bool prediction_stable{true};
  bool match_kind_stable{true};
};

struct ConfigRun final {
  Config config{};
  std::array<CardRun, 3U> cards{};
  std::size_t all_three_correct_rounds{0U};
  std::vector<double> preprocess_latencies{};
  std::vector<double> ocr_latencies{};
  std::vector<double> match_latencies{};
  std::vector<double> total_latencies{};
};

[[nodiscard]] std::string JsonEscape(const std::string_view input) {
  std::ostringstream output;
  for (const unsigned char value : input) {
    switch (value) {
      case '"': output << "\\\""; break;
      case '\\': output << "\\\\"; break;
      case '\b': output << "\\b"; break;
      case '\f': output << "\\f"; break;
      case '\n': output << "\\n"; break;
      case '\r': output << "\\r"; break;
      case '\t': output << "\\t"; break;
      default:
        if (value < 0x20U) {
          output << "\\u" << std::hex << std::uppercase << std::setw(4)
                 << std::setfill('0') << static_cast<unsigned int>(value)
                 << std::dec << std::nouppercase << std::setfill(' ');
        } else {
          output << static_cast<char>(value);
        }
    }
  }
  return output.str();
}

[[nodiscard]] std::string JsonString(const std::string_view input) {
  return "\"" + JsonEscape(input) + "\"";
}

[[nodiscard]] std::string JsonOptional(
    const std::optional<std::string>& input) {
  return input.has_value() ? JsonString(*input) : "null";
}

[[nodiscard]] std::string MarkdownEscape(std::string input) {
  std::string output;
  output.reserve(input.size());
  for (const char value : input) {
    if (value == '|') {
      output.append("\\|");
    } else if (value == '\r') {
      continue;
    } else if (value == '\n') {
      output.append("\\n");
    } else {
      output.push_back(value);
    }
  }
  return output.empty() ? "<empty>" : output;
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

[[nodiscard]] std::string ThresholdName(
    const lol_assistant::vision::TitleThresholdMode mode) {
  using lol_assistant::vision::TitleThresholdMode;
  switch (mode) {
    case TitleThresholdMode::None: return "none";
    case TitleThresholdMode::Fixed: return "fixed";
    case TitleThresholdMode::Otsu: return "otsu";
  }
  return "invalid";
}

[[nodiscard]] int KindRank(const TextMatchKind kind) noexcept {
  switch (kind) {
    case TextMatchKind::Exact: return 3;
    case TextMatchKind::Normalized: return 2;
    case TextMatchKind::Fuzzy: return 1;
    case TextMatchKind::Unknown: return 0;
  }
  return 0;
}

[[nodiscard]] bool Better(const TextMatchResult& candidate,
                          const TextMatchResult& current) noexcept {
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
  std::vector<InputCandidate> inputs;
  const auto append = [&inputs](std::string value, std::string source,
                                const std::size_t maximum_bytes) {
    if (value.empty() || value.size() > maximum_bytes ||
        std::find_if(inputs.begin(), inputs.end(), [&](const auto& item) {
          return item.value == value;
        }) != inputs.end()) {
      return;
    }
    inputs.push_back({std::move(value), std::move(source)});
  };
  constexpr std::size_t maximum_aggregate =
      lol_assistant::vision::kMaximumOcrLineCandidates *
      lol_assistant::vision::kMaximumOcrLineCandidateBytes;
  append(ocr.raw_text, "raw_text", maximum_aggregate);
  const auto count = std::min<std::size_t>(
      ocr.line_candidates.size(),
      lol_assistant::vision::kMaximumOcrLineCandidates);
  for (std::size_t index = 0U; index < count; ++index) {
    append(ocr.line_candidates[index], "line_" + std::to_string(index),
           lol_assistant::vision::kMaximumOcrLineCandidateBytes);
  }
  for (std::size_t index = 0U; index + 1U < count; ++index) {
    if (ocr.line_candidates[index].empty() ||
        ocr.line_candidates[index + 1U].empty() ||
        ocr.line_candidates[index].size() >
            lol_assistant::vision::kMaximumOcrLineCandidateBytes ||
        ocr.line_candidates[index + 1U].size() >
            lol_assistant::vision::kMaximumOcrLineCandidateBytes) {
      continue;
    }
    append(ocr.line_candidates[index] + " " +
               ocr.line_candidates[index + 1U],
           "adjacent_lines_" + std::to_string(index) + "_" +
               std::to_string(index + 1U),
           2U * lol_assistant::vision::kMaximumOcrLineCandidateBytes + 1U);
  }

  MatchSummary summary;
  bool diagnostic = false;
  for (const auto& input : inputs) {
    auto result = matcher.Match(input.value, candidates);
    if (!result.matched()) {
      if (!summary.matched &&
          (!diagnostic || result.top1_score > summary.best.top1_score)) {
        summary.best = std::move(result);
        summary.selected_input = input.value;
        summary.selected_source = input.source;
        diagnostic = true;
      }
      continue;
    }
    if (summary.matched && result.id != summary.best.id) {
      summary.conflicting_ids = true;
      if (Better(result, summary.best)) {
        summary.best = std::move(result);
        summary.selected_input = input.value;
        summary.selected_source = input.source;
      }
      continue;
    }
    if (!summary.matched || Better(result, summary.best)) {
      summary.best = std::move(result);
      summary.selected_input = input.value;
      summary.selected_source = input.source;
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
      const auto gray =
          image.pixels[static_cast<std::size_t>(y) * image.stride + x];
      const auto offset = static_cast<std::size_t>(y) * crop.stride + x * 4U;
      crop.pixels[offset + 0U] = gray;
      crop.pixels[offset + 1U] = gray;
      crop.pixels[offset + 2U] = gray;
      crop.pixels[offset + 3U] = 255U;
    }
  }
  if (!crop.IsValid()) {
    throw std::runtime_error("invalid BGRA adapter output");
  }
  return crop;
}

[[nodiscard]] double Milliseconds(const Clock::time_point begin,
                                  const Clock::time_point end) {
  return std::chrono::duration<double, std::milli>(end - begin).count();
}

[[nodiscard]] double Average(const std::vector<double>& values) {
  double sum = 0.0;
  for (const double value : values) sum += value;
  return values.empty() ? 0.0 : sum / static_cast<double>(values.size());
}

[[nodiscard]] double P95(std::vector<double> values) {
  if (values.empty()) return 0.0;
  std::sort(values.begin(), values.end());
  const auto rank = static_cast<std::size_t>(
      std::ceil(0.95 * static_cast<double>(values.size())));
  return values[std::max<std::size_t>(1U, rank) - 1U];
}

[[nodiscard]] OwningBgraCrop Crop(const lol_assistant::common::Frame& frame,
                                  const PixelRect rect) {
  const lol_assistant::detector::PixelRoi roi{rect.x, rect.y, rect.width,
                                               rect.height};
  auto result = lol_assistant::detector::CropRawBgraOwning(frame, roi);
  if (!result.ok()) {
    throw std::runtime_error("manual title crop failed: " + result.reason);
  }
  return std::move(*result.value);
}

[[nodiscard]] Observation RunOne(
    const Config& config, const OwningBgraCrop& raw_crop,
    const lol_assistant::vision::WindowsMediaOcrTitleRecognizer& ocr,
    const lol_assistant::vision::BoundedTextMatcher& matcher,
    const std::vector<lol_assistant::vision::TitleCandidate>& candidates) {
  Observation observation;
  const auto total_begin = Clock::now();
  OwningBgraCrop processed_crop;
  const OwningBgraCrop* input = &raw_crop;
  const auto preprocess_begin = total_begin;
  if (!config.raw) {
    const auto result = lol_assistant::vision::TitlePreprocessor::Process(
        raw_crop, config.parameters);
    if (!result.ok()) {
      throw std::runtime_error("preprocess failed for " + config.id + ": " +
                               result.reason);
    }
    observation.applied = result.value->parameters;
    processed_crop = GrayToBgra(result.value->image);
    input = &processed_crop;
  }
  const auto preprocess_end = Clock::now();
  observation.ocr = ocr.Recognize(*input);
  const auto ocr_end = Clock::now();
  if (observation.ocr.ok()) {
    observation.match = MatchOcr(observation.ocr, matcher, candidates);
    if (observation.match.matched && !observation.match.conflicting_ids) {
      observation.prediction = observation.match.best.id;
    }
  } else {
    observation.match.best.reason = observation.ocr.reason;
  }
  const auto match_end = Clock::now();
  observation.timing.preprocess_ms =
      Milliseconds(preprocess_begin, preprocess_end);
  observation.timing.ocr_ms = Milliseconds(preprocess_end, ocr_end);
  observation.timing.match_ms = Milliseconds(ocr_end, match_end);
  observation.timing.total_ms = Milliseconds(total_begin, match_end);
  return observation;
}

[[nodiscard]] bool SameOcrText(
    const lol_assistant::vision::OcrTextResult& left,
    const lol_assistant::vision::OcrTextResult& right) {
  return left.state == right.state && left.raw_text == right.raw_text &&
         left.line_candidates == right.line_candidates;
}

[[nodiscard]] ConfigRun RunConfig(
    const Config& config, const std::array<OwningBgraCrop, 3U>& crops,
    const lol_assistant::vision::WindowsMediaOcrTitleRecognizer& ocr,
    const lol_assistant::vision::BoundedTextMatcher& matcher,
    const std::vector<lol_assistant::vision::TitleCandidate>& candidates) {
  ConfigRun run;
  run.config = config;
  for (std::size_t card = 0U; card < kCards.size(); ++card) {
    run.cards[card].spec = kCards[card];
    static_cast<void>(RunOne(config, crops[card], ocr, matcher, candidates));
  }
  for (std::size_t repeat = 0U; repeat < kMeasuredRepeats; ++repeat) {
    bool all_three_correct = true;
    for (std::size_t card = 0U; card < kCards.size(); ++card) {
      auto observation = RunOne(config, crops[card], ocr, matcher, candidates);
      auto& card_run = run.cards[card];
      const bool correct =
          observation.prediction == std::string{kCards[card].truth_id};
      all_three_correct = all_three_correct && correct;
      card_run.correct_repeats += correct;
      card_run.exact_repeats +=
          observation.match.best.kind == TextMatchKind::Exact;
      card_run.normalized_repeats +=
          observation.match.best.kind == TextMatchKind::Normalized;
      card_run.fuzzy_repeats +=
          observation.match.best.kind == TextMatchKind::Fuzzy;
      card_run.unknown_repeats += !observation.prediction.has_value();
      if (repeat == 0U) {
        card_run.canonical = observation;
      } else {
        card_run.ocr_text_stable =
            card_run.ocr_text_stable &&
            SameOcrText(card_run.canonical.ocr, observation.ocr);
        card_run.prediction_stable =
            card_run.prediction_stable &&
            card_run.canonical.prediction == observation.prediction;
        card_run.match_kind_stable =
            card_run.match_kind_stable &&
            card_run.canonical.match.best.kind == observation.match.best.kind;
      }
      run.preprocess_latencies.push_back(observation.timing.preprocess_ms);
      run.ocr_latencies.push_back(observation.timing.ocr_ms);
      run.match_latencies.push_back(observation.timing.match_ms);
      run.total_latencies.push_back(observation.timing.total_ms);
    }
    run.all_three_correct_rounds += all_three_correct;
  }
  return run;
}

[[nodiscard]] std::size_t CorrectCards(const ConfigRun& run) {
  return static_cast<std::size_t>(std::count_if(
      run.cards.begin(), run.cards.end(), [](const CardRun& card) {
        return card.canonical.prediction == std::string{card.spec.truth_id};
      }));
}

[[nodiscard]] std::size_t KindCards(const ConfigRun& run,
                                    const TextMatchKind kind) {
  return static_cast<std::size_t>(std::count_if(
      run.cards.begin(), run.cards.end(), [kind](const CardRun& card) {
        return card.canonical.match.best.kind == kind;
      }));
}

[[nodiscard]] std::size_t CorrectRepeats(const ConfigRun& run) {
  std::size_t value = 0U;
  for (const auto& card : run.cards) value += card.correct_repeats;
  return value;
}

[[nodiscard]] std::size_t ExactRepeats(const ConfigRun& run) {
  std::size_t value = 0U;
  for (const auto& card : run.cards) value += card.exact_repeats;
  return value;
}

[[nodiscard]] std::string UtcNow() {
  const auto now = std::chrono::system_clock::now();
  const std::time_t value = std::chrono::system_clock::to_time_t(now);
  std::tm utc{};
  gmtime_s(&utc, &value);
  std::ostringstream output;
  output << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
  return output.str();
}

void WriteRect(std::ostream& output, const PixelRect rect) {
  output << "{\"x\":" << rect.x << ",\"y\":" << rect.y
         << ",\"width\":" << rect.width << ",\"height\":"
         << rect.height << '}';
}

void WriteConfigJson(std::ostream& output, const Config& config) {
  output << "{\"variant_id\":" << JsonString(config.id)
         << ",\"raw_reference\":" << (config.raw ? "true" : "false");
  if (!config.raw) {
    output << ",\"contrast_stretch\":"
           << (config.parameters.contrast_stretch ? "true" : "false")
           << ",\"threshold_mode\":"
           << JsonString(ThresholdName(config.parameters.threshold_mode))
           << ",\"fixed_threshold\":";
    if (config.parameters.threshold_mode ==
        lol_assistant::vision::TitleThresholdMode::Fixed) {
      output << static_cast<unsigned int>(config.parameters.fixed_threshold);
    } else {
      output << "null";
    }
    output << ",\"scale\":" << config.parameters.scale
           << ",\"grayscale_formula\":"
           << JsonString("(29*B + 150*G + 77*R) >> 8")
           << ",\"operation_order\":"
           << JsonString("grayscale -> optional contrast stretch -> optional "
                         "threshold -> nearest-neighbor scale");
  }
  output << '}';
}

void WriteLatencyJson(std::ostream& output, const ConfigRun& run) {
  output << std::fixed << std::setprecision(4)
         << "{\"sample_count\":" << run.total_latencies.size()
         << ",\"preprocess_ms\":{\"avg\":"
         << Average(run.preprocess_latencies) << ",\"p95\":"
         << P95(run.preprocess_latencies) << "},\"ocr_ms\":{\"avg\":"
         << Average(run.ocr_latencies) << ",\"p95\":"
         << P95(run.ocr_latencies) << "},\"match_ms\":{\"avg\":"
         << Average(run.match_latencies) << ",\"p95\":"
         << P95(run.match_latencies) << "},\"end_to_end_ms\":{\"avg\":"
         << Average(run.total_latencies) << ",\"p95\":"
         << P95(run.total_latencies) << "}}";
}

void WriteRunJson(std::ostream& output, const ConfigRun& run) {
  output << "{\"config\":";
  WriteConfigJson(output, run.config);
  output << ",\"accuracy\":{\"correct_cards\":" << CorrectCards(run)
         << ",\"total_cards\":3,\"all_three_correct\":"
         << (CorrectCards(run) == 3U ? "true" : "false")
         << ",\"exact\":" << KindCards(run, TextMatchKind::Exact)
         << ",\"normalized\":"
         << KindCards(run, TextMatchKind::Normalized) << ",\"fuzzy\":"
         << KindCards(run, TextMatchKind::Fuzzy) << ",\"unknown\":"
         << KindCards(run, TextMatchKind::Unknown)
         << ",\"measured_repeats\":" << kMeasuredRepeats
         << ",\"correct_card_repeats\":" << CorrectRepeats(run)
         << ",\"total_card_repeats\":" << 3U * kMeasuredRepeats
         << ",\"all_three_correct_rounds\":"
         << run.all_three_correct_rounds << ",\"total_rounds\":"
         << kMeasuredRepeats << "},\"latency\":";
  WriteLatencyJson(output, run);
  output << ",\"cards\":[";
  for (std::size_t index = 0U; index < run.cards.size(); ++index) {
    if (index != 0U) output << ',';
    const auto& card = run.cards[index];
    const auto& observation = card.canonical;
    output << "{\"slot\":" << JsonString(card.spec.slot)
           << ",\"truth\":{\"id\":" << JsonString(card.spec.truth_id)
           << ",\"title\":" << JsonString(card.spec.truth_title)
           << "},\"title_roi\":";
    WriteRect(output, card.spec.title_roi);
    output << ",\"ocr\":{\"state\":"
           << JsonString(observation.ocr.ok() ? "success" : "failure")
           << ",\"raw_text\":" << JsonString(observation.ocr.raw_text)
           << ",\"line_candidates\":[";
    for (std::size_t line = 0U;
         line < observation.ocr.line_candidates.size(); ++line) {
      if (line != 0U) output << ',';
      output << JsonString(observation.ocr.line_candidates[line]);
    }
    output << "],\"backend\":" << JsonString(observation.ocr.backend)
           << ",\"confidence\":null,\"reason\":"
           << JsonString(observation.ocr.reason)
           << "},\"mapping\":{\"selected_input\":"
           << JsonString(observation.match.selected_input)
           << ",\"selected_input_source\":"
           << JsonString(observation.match.selected_source)
           << ",\"normalized_text\":"
           << JsonString(observation.match.best.normalized_text)
           << ",\"predicted_id\":" << JsonOptional(observation.prediction)
           << ",\"predicted_title\":";
    if (observation.match.best.title.has_value()) {
      output << JsonString(*observation.match.best.title);
    } else {
      output << "null";
    }
    output << ",\"match_kind\":"
           << JsonString(KindName(observation.match.best.kind))
           << ",\"reason\":" << JsonString(observation.match.best.reason)
           << ",\"top1_score\":" << std::fixed << std::setprecision(4)
           << observation.match.best.top1_score << ",\"top2_score\":"
           << observation.match.best.top2_score << ",\"margin\":"
           << observation.match.best.margin
           << ",\"conflicting_ids\":"
           << (observation.match.conflicting_ids ? "true" : "false")
           << "},\"repeat_stability\":{\"ocr_text_stable\":"
           << (card.ocr_text_stable ? "true" : "false")
           << ",\"prediction_stable\":"
           << (card.prediction_stable ? "true" : "false")
           << ",\"match_kind_stable\":"
           << (card.match_kind_stable ? "true" : "false")
           << ",\"correct_repeats\":" << card.correct_repeats
           << ",\"exact_repeats\":" << card.exact_repeats
           << ",\"normalized_repeats\":" << card.normalized_repeats
           << ",\"fuzzy_repeats\":" << card.fuzzy_repeats
           << ",\"unknown_repeats\":" << card.unknown_repeats << '}';
    if (observation.applied.has_value()) {
      output << ",\"applied_preprocess\":{\"contrast_black_point\":"
             << static_cast<unsigned int>(
                    observation.applied->contrast_black_point)
             << ",\"contrast_white_point\":"
             << static_cast<unsigned int>(
                    observation.applied->contrast_white_point)
             << ",\"resolved_threshold\":";
      if (observation.applied->resolved_threshold.has_value()) {
        output << static_cast<unsigned int>(
            *observation.applied->resolved_threshold);
      } else {
        output << "null";
      }
      output << '}';
    }
    output << '}';
  }
  output << "]}";
}

[[nodiscard]] std::string CardMarkdown(const CardRun& card) {
  const auto& observation = card.canonical;
  std::string output = MarkdownEscape(observation.ocr.raw_text);
  output.append(" → ");
  output.append(observation.prediction.value_or("UNKNOWN"));
  output.push_back('(');
  output.append(KindName(observation.match.best.kind));
  output.push_back(')');
  return output;
}

void WriteReports(const fs::path& root, const std::string& backend_reason,
                  const std::size_t candidate_count,
                  const ConfigRun& raw_reference,
                  const std::vector<ConfigRun>& variants,
                  const std::vector<std::size_t>& ranking) {
  const auto& best = variants[ranking.front()];
  const bool best_complete =
      best.all_three_correct_rounds == kMeasuredRepeats &&
      CorrectCards(best) == 3U;
  const fs::path json_path =
      root / "outputs/phase2_real_ocr_benchmark.json";
  std::ofstream json(json_path, std::ios::binary | std::ios::trunc);
  if (!json) throw std::runtime_error("cannot write JSON report");
  json << "{\n  \"schema\":\"phase2.real_ocr_parameter_benchmark\",\n"
       << "  \"version\":1,\n  \"generated_at_utc\":"
       << JsonString(UtcNow()) << ",\n  \"scope\":{\"sample_id\":"
       << JsonString(kSampleId) << ",\"raw_path\":"
       << JsonString(kSampleRelativePath)
       << ",\"resolution\":{\"width\":2560,\"height\":1600},"
          "\"clear_frames\":1,\"cards\":3,\"independent_offers\":1,"
          "\"correlation_note\":"
       << JsonString("one frame and one offer; the three card titles are not "
                     "independent offers")
       << "},\n  \"ground_truth_source\":"
       << JsonString("user-supplied labels; used only for scoring, never as "
                     "predictions")
       << ",\n  \"manual_title_rois\":[";
  for (std::size_t index = 0U; index < kCards.size(); ++index) {
    if (index != 0U) json << ',';
    json << "{\"slot\":" << JsonString(kCards[index].slot)
         << ",\"truth_id\":" << JsonString(kCards[index].truth_id)
         << ",\"truth_title\":" << JsonString(kCards[index].truth_title)
         << ",\"measured_glyph_bbox\":";
    WriteRect(json, kCards[index].glyph_bbox);
    json << ",\"ocr_roi\":";
    WriteRect(json, kCards[index].title_roi);
    json << '}';
  }
  json << "],\n  \"roi_method\":"
       << JsonString("manual measurement on n5; threshold-assisted glyph bbox "
                     "check at luma>175, then 8 px horizontal and 6-7 px "
                     "vertical dark-background margin; title only")
       << ",\n  \"ocr_backend\":{\"backend\":"
       << JsonString(lol_assistant::vision::kWindowsMediaOcrZhCnBackend)
       << ",\"probe_reason\":" << JsonString(backend_reason)
       << ",\"confidence_exposed\":false},\n  \"vocabulary\":{\"path\":"
       << JsonString("data/knowledge/augments.zh-CN.json")
       << ",\"mode\":\"KIWI\",\"candidate_count\":" << candidate_count
       << ",\"matcher\":{\"maximum_edit_distance\":2,"
          "\"maximum_edit_ratio\":0.25,\"minimum_fuzzy_length\":4,"
          "\"minimum_fuzzy_score\":0.72,"
          "\"minimum_top1_top2_margin\":0.12}},\n"
       << "  \"protocol\":{\"variants\":18,\"warmup_runs_per_card\":1,"
          "\"measured_runs_per_card\":"
       << kMeasuredRepeats
       << ",\"latency_samples_per_config\":" << 3U * kMeasuredRepeats
       << ",\"p95_method\":\"nearest-rank\","
          "\"end_to_end_includes\":"
       << JsonString("TitlePreprocessor + gray-to-BGRA adapter + "
                     "Windows.Media.Ocr + vocabulary match")
       << ",\"accuracy_note\":"
       << JsonString("repeats characterize stability and latency; they do not "
                     "increase the independent offer count")
       << "},\n  \"raw_reference\":";
  WriteRunJson(json, raw_reference);
  json << ",\n  \"variants\":[";
  for (std::size_t index = 0U; index < variants.size(); ++index) {
    if (index != 0U) json << ',';
    WriteRunJson(json, variants[index]);
  }
  json << "],\n  \"ranking\":[";
  for (std::size_t rank = 0U; rank < ranking.size(); ++rank) {
    if (rank != 0U) json << ',';
    const auto& run = variants[ranking[rank]];
    json << "{\"rank\":" << rank + 1U << ",\"variant_id\":"
         << JsonString(run.config.id) << ",\"correct_cards\":"
         << CorrectCards(run) << ",\"all_three_correct_rounds\":"
         << run.all_three_correct_rounds << ",\"exact_card_repeats\":"
         << ExactRepeats(run) << ",\"end_to_end_avg_ms\":" << std::fixed
         << std::setprecision(4) << Average(run.total_latencies) << '}';
  }
  json << "],\n  \"selection\":{\"status\":\"UNKNOWN\",\"reason\":"
       << JsonString("only one independent real offer; observed ranking cannot "
                     "establish a general product optimum")
       << ",\"best_observed_variant\":" << JsonString(best.config.id)
       << ",\"best_observed_completed_all_rounds\":"
       << (best_complete ? "true" : "false")
       << ",\"provisional_exact_config\":";
  WriteConfigJson(json, best.config);
  json << "}\n}\n";
  json.close();

  const fs::path markdown_path =
      root / "outputs/phase2_real_ocr_benchmark.md";
  std::ofstream markdown(markdown_path, std::ios::binary | std::ios::trunc);
  if (!markdown) throw std::runtime_error("cannot write Markdown report");
  markdown << "# Phase2 n5 真实 OCR 参数 Benchmark\n\n"
           << "## 结论\n\n"
           << "- 产品最优参数结论：**UNKNOWN**。本报告只有 1 个真实独立 "
              "offer，不能把 3 张卡或 7 次计时重复伪装成多个独立 offer。\n"
           << "- n5 上观测排名第一：`" << best.config.id << "`；3 卡首轮 "
           << CorrectCards(best) << "/3，7 个测量轮次中全三卡正确 "
           << best.all_three_correct_rounds << "/" << kMeasuredRepeats
           << "。\n"
           << "- 可供下一步产品试用的精确候选：contrast_stretch=`"
           << (best.config.parameters.contrast_stretch ? "true" : "false")
           << "`，threshold_mode=`"
           << ThresholdName(best.config.parameters.threshold_mode)
           << "`，fixed_threshold=`";
  if (best.config.parameters.threshold_mode ==
      lol_assistant::vision::TitleThresholdMode::Fixed) {
    markdown << static_cast<unsigned int>(best.config.parameters.fixed_threshold);
  } else {
    markdown << "N/A";
  }
  markdown << "`，scale=`" << best.config.parameters.scale
           << "x`，缩放=`nearest-neighbor`；这只是 n5 最佳观测，不是已泛化的 "
              "产品定案。\n\n"
           << "## 18 变体事实表\n\n"
           << "OCR 单元格格式为 `Windows.Media.Ocr 原文 → 产品词库 ID(映射类型)`。"
              "3/3 使用每个配置的首个测量轮；稳定性列是 7 个轮次的三卡全对数。\n\n"
           << "| Rank | Variant | Left | Center | Right | 3/3 | 全对轮次 | "
              "E2E avg ms | E2E p95 ms |\n"
           << "|---:|---|---|---|---|---:|---:|---:|---:|\n";
  for (std::size_t rank = 0U; rank < ranking.size(); ++rank) {
    const auto& run = variants[ranking[rank]];
    markdown << '|' << rank + 1U << "|`" << run.config.id << "`|"
             << CardMarkdown(run.cards[0]) << '|' << CardMarkdown(run.cards[1])
             << '|' << CardMarkdown(run.cards[2]) << '|' << CorrectCards(run)
             << "/3|" << run.all_three_correct_rounds << '/'
             << kMeasuredRepeats << '|' << std::fixed << std::setprecision(3)
             << Average(run.total_latencies) << '|' << P95(run.total_latencies)
             << "|\n";
  }
  markdown << "\n## Raw 对照（不计入 18 变体排名）\n\n"
           << "| Left | Center | Right | 3/3 | 全对轮次 | E2E avg ms | E2E "
              "p95 ms |\n"
           << "|---|---|---|---:|---:|---:|---:|\n|"
           << CardMarkdown(raw_reference.cards[0]) << '|'
           << CardMarkdown(raw_reference.cards[1]) << '|'
           << CardMarkdown(raw_reference.cards[2]) << '|'
           << CorrectCards(raw_reference) << "/3|"
           << raw_reference.all_three_correct_rounds << '/'
           << kMeasuredRepeats << '|' << Average(raw_reference.total_latencies)
           << '|' << P95(raw_reference.total_latencies) << "|\n\n"
           << "## 人工标题 ROI（2560x1600 原始像素）\n\n"
           << "| Slot | 人工标签 | Glyph bbox | OCR ROI |\n"
           << "|---|---|---|---|\n";
  for (const auto& card : kCards) {
    markdown << '|' << card.slot << '|' << card.truth_title << " → `"
             << card.truth_id << "`|`" << card.glyph_bbox.x << ','
             << card.glyph_bbox.y << ',' << card.glyph_bbox.width << ','
             << card.glyph_bbox.height << "`|`" << card.title_roi.x << ','
             << card.title_roi.y << ',' << card.title_roi.width << ','
             << card.title_roi.height << "`|\n";
  }
  markdown << "\n人工标签只参与评分，未作为 OCR 预测输入。ROI 是 n5 可见字形的人工 "
              "测量框，并留 8 px 水平、6–7 px 垂直暗背景边距。\n\n"
           << "## 方法与边界\n\n"
           << "- 样本：仅 `" << kSampleId
           << "`，1 帧、1 offer、3 卡；同一 offer 内卡片高度相关。\n"
           << "- 后端：`windows_media_ocr:zh-CN`（probe=`" << backend_reason
           << "`），该 API 不提供 confidence，因此 JSON 中保持 `null`。\n"
           << "- 词库：`data/knowledge/augments.zh-CN.json`，mode=`KIWI`，"
           << candidate_count << " 个候选；直接编译产品 `BoundedTextMatcher`，"
              "按 exact → normalized → 有界 fuzzy 映射。\n"
           << "- 预处理：直接编译产品 `TitlePreprocessor`，完整枚举 2×3×3=18 "
              "项；处理顺序为灰度、可选 min/max contrast、可选 fixed-128/Otsu、"
              "nearest-neighbor 1x/2x/3x。\n"
           << "- 时延：每卡 1 次 warm-up 后测 7 次，每配置 21 个调用；p95 为 "
              "nearest-rank。E2E 包含预处理、gray→BGRA、Windows.Media.Ocr 和词库"
              "匹配。重复只用于稳定性/时延，不增加独立准确率样本量。\n"
           << "- 逐卡 line candidates、normalized text、match score、实际 Otsu "
              "threshold 与 contrast black/white point 见配套 JSON。\n";
}

}  // namespace

int wmain(int argc, wchar_t* argv[]) {
  try {
    const fs::path root = argc > 1 ? fs::absolute(argv[1]) : fs::current_path();
    const fs::path sample_path = root / fs::path{kSampleRelativePath};
    const auto frame = lol_assistant::replay::WicImageCodec::Decode(
        sample_path,
        {lol_assistant::common::FrameSourceKind::Replay, std::string{kSampleId}},
        1U);
    if (frame.width != 2560U || frame.height != 1600U) {
      throw std::runtime_error("n5 resolution is not 2560x1600");
    }
    std::array<OwningBgraCrop, 3U> crops;
    for (std::size_t index = 0U; index < crops.size(); ++index) {
      crops[index] = Crop(frame, kCards[index].title_roi);
    }

    const auto catalog_result = lol_assistant::knowledge::LoadAugmentCatalog(
        root / "data/knowledge/augments.zh-CN.json");
    if (!catalog_result.ok()) {
      throw std::runtime_error("catalog load failed: " + catalog_result.reason);
    }
    const auto candidates = lol_assistant::vision::BuildTitleCandidates(
        *catalog_result.catalog, "KIWI");
    lol_assistant::vision::BoundedTextMatcher matcher;
    lol_assistant::vision::WindowsMediaOcrTitleRecognizer ocr;
    const auto backend = ocr.Probe();
    if (!backend.available()) {
      throw std::runtime_error("OCR backend unavailable: " + backend.reason);
    }

    const auto enumeration =
        lol_assistant::vision::TitlePreprocessor::EnumerateVariants(crops[0]);
    if (!enumeration.ok() || enumeration.variants.size() != 18U) {
      throw std::runtime_error("TitlePreprocessor did not enumerate 18 variants");
    }
    std::vector<Config> configs;
    configs.reserve(enumeration.variants.size());
    for (const auto& variant : enumeration.variants) {
      configs.push_back({variant.id, false, variant.parameters.requested});
    }

    const Config raw_config{"raw_bgra", true, {}};
    std::cerr << "Running raw reference...\n";
    const auto raw_reference =
        RunConfig(raw_config, crops, ocr, matcher, candidates);
    std::vector<ConfigRun> variants;
    variants.reserve(configs.size());
    for (std::size_t index = 0U; index < configs.size(); ++index) {
      std::cerr << "Running variant " << index + 1U << "/18: "
                << configs[index].id << "...\n";
      variants.push_back(RunConfig(configs[index], crops, ocr, matcher,
                                   candidates));
    }

    std::vector<std::size_t> ranking(variants.size());
    for (std::size_t index = 0U; index < ranking.size(); ++index) {
      ranking[index] = index;
    }
    std::stable_sort(ranking.begin(), ranking.end(),
                     [&](const std::size_t left, const std::size_t right) {
      const auto& a = variants[left];
      const auto& b = variants[right];
      if (a.all_three_correct_rounds != b.all_three_correct_rounds) {
        return a.all_three_correct_rounds > b.all_three_correct_rounds;
      }
      if (CorrectRepeats(a) != CorrectRepeats(b)) {
        return CorrectRepeats(a) > CorrectRepeats(b);
      }
      if (ExactRepeats(a) != ExactRepeats(b)) {
        return ExactRepeats(a) > ExactRepeats(b);
      }
      return Average(a.total_latencies) < Average(b.total_latencies);
    });

    WriteReports(root, backend.reason, candidates.size(), raw_reference,
                 variants, ranking);
    const auto& best = variants[ranking.front()];
    std::cout << "BEST_OBSERVED=" << best.config.id
              << " CORRECT=" << CorrectCards(best) << "/3 ALL_ROUNDS="
              << best.all_three_correct_rounds << '/' << kMeasuredRepeats
              << " AVG_MS=" << std::fixed << std::setprecision(3)
              << Average(best.total_latencies) << " P95_MS="
              << P95(best.total_latencies) << '\n';
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "fatal: " << error.what() << '\n';
    return 1;
  }
}
