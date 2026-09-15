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
#include "lol_assistant/detector/roi.h"
#include "lol_assistant/replay/wic_image_codec.h"
#include "lol_assistant/vision/icon_matcher.h"

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using lol_assistant::common::FrameSource;
using lol_assistant::common::FrameSourceKind;
using lol_assistant::detector::OwningBgraCrop;
using lol_assistant::detector::PixelRect;
using lol_assistant::detector::PixelRoi;
using lol_assistant::vision::IconMatchResult;
using lol_assistant::vision::IconMatchState;

namespace {

constexpr std::size_t kTimedIterations = 2000U;
constexpr std::size_t kWarmupIterations = 100U;
constexpr std::array<std::string_view, 3U> kSlots{"left", "center", "right"};
constexpr std::array<std::string_view, 3U> kTruth{
    "ARAM_Impassable", "Equilibrium", "ARAM_CelestialBody"};
constexpr std::array<PixelRect, 3U> kCurrentRects{{
    {512U, 553U, 256U, 406U},
    {1152U, 553U, 257U, 406U},
    {1792U, 553U, 256U, 406U},
}};
// Measured from RAW icon alpha silhouettes. The three x origins preserve the
// observed 546 px slot pitch; no matcher score was used to select this ROI.
constexpr std::array<PixelRect, 3U> kCalibratedRects{{
    {625U, 342U, 240U, 240U},
    {1171U, 342U, 240U, 240U},
    {1717U, 342U, 240U, 240U},
}};

[[nodiscard]] std::string ReadAll(const fs::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw std::runtime_error("cannot open " + path.string());
  }
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
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
LoadTemplates(const fs::path& path) {
  const std::string input = ReadAll(path);
  std::vector<lol_assistant::vision::IconHashTemplate> templates;
  std::size_t position = 0U;
  while ((position = input.find("\"augment_id\": \"", position)) !=
         std::string::npos) {
    const auto next = input.find("\"augment_id\": \"", position + 1U);
    const auto object_end = next == std::string::npos ? input.size() : next;
    const std::string_view object{input.data() + position,
                                  object_end - position};
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
  return templates;
}

[[nodiscard]] OwningBgraCrop Crop(
    const lol_assistant::common::Frame& frame, const PixelRect rect) {
  auto result = lol_assistant::detector::CropRawBgraOwning(
      frame, PixelRoi{rect.x, rect.y, rect.width, rect.height});
  if (!result.ok()) {
    throw std::runtime_error("crop failed: " + result.reason);
  }
  return std::move(*result.value);
}

[[nodiscard]] const char* StateName(const IconMatchState state) noexcept {
  switch (state) {
    case IconMatchState::Unavailable:
      return "unavailable";
    case IconMatchState::Unknown:
      return "unknown";
    case IconMatchState::Matched:
      return "matched";
  }
  return "invalid";
}

[[nodiscard]] double Average(const std::vector<double>& values) {
  double total = 0.0;
  for (const double value : values) {
    total += value;
  }
  return values.empty() ? 0.0 : total / static_cast<double>(values.size());
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

void EmitOptional(const std::optional<float> value) {
  if (value.has_value()) {
    std::cout << *value;
  } else {
    std::cout << '-';
  }
}

void BenchmarkVariant(
    const char* variant, const std::array<PixelRect, 3U>& rects,
    const lol_assistant::common::Frame& frame,
    const lol_assistant::vision::PerceptualHashTemplateMatcher& matcher) {
  std::vector<double> variant_latencies;
  variant_latencies.reserve(kTimedIterations * rects.size());
  std::size_t checksum = 0U;
  for (std::size_t slot = 0U; slot < rects.size(); ++slot) {
    const auto crop = Crop(frame, rects[slot]);
    const auto hash = lol_assistant::vision::ComputeDifferenceHash(crop);
    if (!hash.ok()) {
      throw std::runtime_error("hash failed: " + hash.reason);
    }
    const IconMatchResult result = matcher.Match(crop, "KIWI");
    for (std::size_t index = 0U; index < kWarmupIterations; ++index) {
      checksum += matcher.Match(crop, "KIWI").candidate_ids.size();
    }

    std::vector<double> card_latencies;
    card_latencies.reserve(kTimedIterations);
    for (std::size_t index = 0U; index < kTimedIterations; ++index) {
      const auto begin = Clock::now();
      const auto timed = matcher.Match(crop, "KIWI");
      const auto end = Clock::now();
      checksum += timed.reason.size();
      const double milliseconds =
          std::chrono::duration<double, std::milli>(end - begin).count();
      card_latencies.push_back(milliseconds);
      variant_latencies.push_back(milliseconds);
    }

    std::cout << "ROW\t" << variant << '\t' << kSlots[slot] << '\t'
              << kTruth[slot] << '\t' << rects[slot].x << '\t'
              << rects[slot].y << '\t' << rects[slot].width << '\t'
              << rects[slot].height << '\t' << StateName(result.state) << '\t'
              << result.augment_id.value_or("-") << '\t' << result.reason
              << '\t' << std::hex << std::setw(16) << std::setfill('0')
              << *hash.hash << std::dec << std::setfill(' ') << '\t';
    EmitOptional(result.top1_score);
    std::cout << '\t';
    EmitOptional(result.top2_score);
    std::cout << '\t';
    EmitOptional(result.margin);
    std::cout << '\t'
              << (result.candidate_ids.empty() ? "-" : result.candidate_ids[0])
              << '\t'
              << (result.candidate_ids.size() < 2U ? "-"
                                                   : result.candidate_ids[1])
              << '\t' << Average(card_latencies) << '\t'
              << P95(card_latencies) << '\n';
  }
  std::cout << "SUMMARY\t" << variant << '\t' << Average(variant_latencies)
            << '\t' << P95(variant_latencies) << '\t'
            << variant_latencies.size() << '\t' << checksum << '\n';
}

}  // namespace

int wmain(int argc, wchar_t* argv[]) {
  try {
    const fs::path root = argc > 1 ? fs::absolute(argv[1]) : fs::current_path();
    const fs::path sample =
        root / "data/dataset/augment_offers/real/"
               "sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6";
    const auto templates =
        LoadTemplates(root / "data/knowledge/augment_icons/manifest.json");
    if (templates.size() != 245U) {
      throw std::runtime_error("expected exactly 245 templates, got " +
                               std::to_string(templates.size()));
    }
    const auto eligible = std::count_if(
        templates.begin(), templates.end(), [](const auto& item) {
          return item.modes.empty() ||
                 std::find(item.modes.begin(), item.modes.end(), "KIWI") !=
                     item.modes.end();
        });
    const lol_assistant::vision::PerceptualHashTemplateMatcher matcher{
        templates};
    const auto frame = lol_assistant::replay::WicImageCodec::Decode(
        sample / "RAW.png", FrameSource{FrameSourceKind::Replay, "n5-raw"},
        1U);
    if (frame.width != 2560U || frame.height != 1600U) {
      throw std::runtime_error("unexpected RAW dimensions");
    }

    std::cout << std::fixed << std::setprecision(6);
    std::cout << "META\t" << templates.size() << '\t' << eligible << '\t'
              << kTimedIterations << '\t' << kWarmupIterations << '\n';
    BenchmarkVariant("current_metadata", kCurrentRects, frame, matcher);
    BenchmarkVariant("calibrated", kCalibratedRects, frame, matcher);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "phase2_real_icon_benchmark: " << error.what() << '\n';
    return 1;
  }
}
