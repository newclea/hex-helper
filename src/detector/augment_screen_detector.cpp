#include "lol_assistant/detector/augment_screen_detector.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

namespace lol_assistant::detector {
namespace {

[[nodiscard]] bool UnitFloat(const float value) noexcept {
  return std::isfinite(value) && value >= 0.0F && value <= 1.0F;
}

[[nodiscard]] bool BoundedRatio(const double value) noexcept {
  return std::isfinite(value) && value >= 0.0 && value <= 0.25;
}

[[nodiscard]] float Clamp01(const float value) noexcept {
  return std::clamp(value, 0.0F, 1.0F);
}

[[nodiscard]] std::uint8_t Luma(const std::uint8_t *pixel) noexcept {
  const std::uint32_t blue = pixel[0];
  const std::uint32_t green = pixel[1];
  const std::uint32_t red = pixel[2];
  return static_cast<std::uint8_t>((29U * blue + 150U * green + 77U * red) >>
                                   8U);
}

struct CardMetrics final {
  float luma{0.0F};
  float edges{0.0F};
  float surround_luma_contrast{0.0F};
  float bright_border_density{0.0F};
  float border_luma_contrast{0.0F};
};

struct PixelAccumulator final {
  std::uint64_t luma_sum{0U};
  std::uint64_t bright_samples{0U};
  std::uint64_t samples{0U};

  void Add(const std::uint8_t luma,
           const std::uint8_t bright_threshold) noexcept {
    luma_sum += luma;
    bright_samples += static_cast<std::uint64_t>(luma >= bright_threshold);
    ++samples;
  }

  [[nodiscard]] float MeanLuma() const noexcept {
    return samples == 0U
               ? 0.0F
               : static_cast<float>(static_cast<double>(luma_sum) /
                                    (255.0 * static_cast<double>(samples)));
  }

  [[nodiscard]] float BrightDensity() const noexcept {
    return samples == 0U
               ? 0.0F
               : static_cast<float>(static_cast<double>(bright_samples) /
                                    static_cast<double>(samples));
  }
};

struct BorderMetrics final {
  float bright_density{0.0F};
  float luma_contrast{0.0F};
};

[[nodiscard]] BorderMetrics
MeasureBorderFrame(const common::Frame &frame, const PixelRoi &roi,
                   const std::uint32_t sample_step,
                   const std::uint8_t bright_threshold) noexcept {
  if (sample_step == 0U || sample_step > roi.width ||
      sample_step > roi.height) {
    return {};
  }
  const std::uint32_t band_x =
      std::max(sample_step, std::max(1U, roi.width / 14U));
  const std::uint32_t band_y =
      std::max(sample_step, std::max(1U, roi.height / 18U));
  const std::uint32_t margin_x = roi.width / 8U;
  const std::uint32_t margin_y = roi.height / 10U;
  if (static_cast<std::uint64_t>(band_x) * 4U >= roi.width ||
      static_cast<std::uint64_t>(band_y) * 4U >= roi.height ||
      static_cast<std::uint64_t>(margin_x) * 2U >= roi.width ||
      static_cast<std::uint64_t>(margin_y) * 2U >= roi.height) {
    return {};
  }

  const auto sample_region =
      [&](const std::uint32_t start_x, const std::uint32_t start_y,
          const std::uint32_t width, const std::uint32_t height) {
        PixelAccumulator measured;
        for (std::uint32_t y = 0U; y < height; y += sample_step) {
          for (std::uint32_t x = 0U; x < width; x += sample_step) {
            const auto offset =
                static_cast<std::size_t>(roi.y + start_y + y) * frame.stride +
                static_cast<std::size_t>(roi.x + start_x + x) *
                    common::Frame::kBytesPerPixel;
            measured.Add(Luma(frame.buffer.data() + offset), bright_threshold);
          }
        }
        return measured;
      };

  const auto top =
      sample_region(margin_x, 0U, roi.width - 2U * margin_x, band_y);
  const auto bottom = sample_region(margin_x, roi.height - band_y,
                                    roi.width - 2U * margin_x, band_y);
  const auto left =
      sample_region(0U, margin_y, band_x, roi.height - 2U * margin_y);
  const auto right = sample_region(roi.width - band_x, margin_y, band_x,
                                   roi.height - 2U * margin_y);
  const auto interior =
      sample_region(2U * band_x, 2U * band_y, roi.width - 4U * band_x,
                    roi.height - 4U * band_y);

  PixelAccumulator border;
  for (const auto *side : {&top, &bottom, &left, &right}) {
    border.luma_sum += side->luma_sum;
    border.bright_samples += side->bright_samples;
    border.samples += side->samples;
  }
  const float weakest_side =
      std::min({top.BrightDensity(), bottom.BrightDensity(),
                left.BrightDensity(), right.BrightDensity()});
  return {weakest_side,
          std::max(0.0F, border.MeanLuma() - interior.MeanLuma())};
}

[[nodiscard]] float
MeasureSurroundLuma(const common::Frame &frame, const PixelRoi &roi,
                    const std::uint32_t sample_step) noexcept {
  const std::uint32_t strip_width =
      std::max(sample_step, std::max(1U, roi.width / 32U));
  std::uint64_t luma_sum = 0U;
  std::uint64_t samples = 0U;

  const auto sample_strip = [&](const std::uint32_t start_x,
                                const std::uint32_t width) {
    for (std::uint32_t y = 0U; y < roi.height; y += sample_step) {
      for (std::uint32_t x = 0U; x < width; x += sample_step) {
        const auto offset = static_cast<std::size_t>(roi.y + y) * frame.stride +
                            static_cast<std::size_t>(start_x + x) *
                                common::Frame::kBytesPerPixel;
        luma_sum += Luma(frame.buffer.data() + offset);
        ++samples;
      }
    }
  };

  if (roi.x >= strip_width) {
    sample_strip(roi.x - strip_width, strip_width);
  }
  const auto right = static_cast<std::uint64_t>(roi.x) + roi.width;
  if (right + strip_width <= frame.width) {
    sample_strip(static_cast<std::uint32_t>(right), strip_width);
  }
  if (samples == 0U) {
    return 0.0F;
  }
  return static_cast<float>(static_cast<double>(luma_sum) /
                            (255.0 * static_cast<double>(samples)));
}

[[nodiscard]] CardMetrics
MeasureCard(const common::Frame &frame, const PixelRoi &roi,
            const std::uint32_t sample_step, const std::uint8_t edge_threshold,
            const std::uint8_t bright_border_threshold) {
  std::uint64_t luma_sum = 0U;
  std::uint64_t samples = 0U;
  std::uint64_t edge_samples = 0U;
  std::uint64_t edges = 0U;
  for (std::uint32_t y = 0U; y < roi.height; y += sample_step) {
    for (std::uint32_t x = 0U; x < roi.width; x += sample_step) {
      const auto offset =
          static_cast<std::size_t>(roi.y + y) * frame.stride +
          static_cast<std::size_t>(roi.x + x) * common::Frame::kBytesPerPixel;
      const auto current = Luma(frame.buffer.data() + offset);
      luma_sum += current;
      ++samples;

      if (x + sample_step < roi.width) {
        const auto right = Luma(frame.buffer.data() + offset +
                                static_cast<std::size_t>(sample_step) *
                                    common::Frame::kBytesPerPixel);
        edges += static_cast<std::uint64_t>(
            std::abs(static_cast<int>(current) - static_cast<int>(right)) >=
            edge_threshold);
        ++edge_samples;
      }
      if (y + sample_step < roi.height) {
        const auto below_offset =
            offset + static_cast<std::size_t>(sample_step) * frame.stride;
        const auto below = Luma(frame.buffer.data() + below_offset);
        edges += static_cast<std::uint64_t>(
            std::abs(static_cast<int>(current) - static_cast<int>(below)) >=
            edge_threshold);
        ++edge_samples;
      }
    }
  }
  if (samples == 0U || edge_samples == 0U) {
    return {};
  }
  const float luma = static_cast<float>(static_cast<double>(luma_sum) /
                                        (255.0 * static_cast<double>(samples)));
  const float surround_luma = MeasureSurroundLuma(frame, roi, sample_step);
  const auto border =
      MeasureBorderFrame(frame, roi, sample_step, bright_border_threshold);
  return {luma,
          static_cast<float>(static_cast<double>(edges) /
                             static_cast<double>(edge_samples)),
          std::abs(luma - surround_luma), border.bright_density,
          border.luma_contrast};
}

template <std::size_t Size>
[[nodiscard]] float Spread(const std::array<float, Size> &values) noexcept {
  const auto [minimum, maximum] =
      std::minmax_element(values.begin(), values.end());
  return *maximum - *minimum;
}

[[nodiscard]] float LayoutConsistency(const ThreeCardRois &rois) noexcept {
  std::array<float, common::kAugmentCardCount> widths{};
  std::array<float, common::kAugmentCardCount> heights{};
  for (std::size_t index = 0U; index < rois.cards.size(); ++index) {
    widths[index] = static_cast<float>(rois.cards[index].width);
    heights[index] = static_cast<float>(rois.cards[index].height);
  }
  const float maximum_width = *std::max_element(widths.begin(), widths.end());
  const float maximum_height =
      *std::max_element(heights.begin(), heights.end());
  if (maximum_width <= 0.0F || maximum_height <= 0.0F) {
    return 0.0F;
  }
  const float size_score = 1.0F - std::max(Spread(widths) / maximum_width,
                                           Spread(heights) / maximum_height);

  const float first_gap = static_cast<float>(
      rois.cards[1].x - (rois.cards[0].x + rois.cards[0].width));
  const float second_gap = static_cast<float>(
      rois.cards[2].x - (rois.cards[1].x + rois.cards[1].width));
  const float maximum_gap = std::max({first_gap, second_gap, 1.0F});
  const float gap_score = 1.0F - std::abs(first_gap - second_gap) / maximum_gap;
  return Clamp01(0.6F * size_score + 0.4F * gap_score);
}

[[nodiscard]] DetectorResult
EvaluateCandidate(const common::Frame &frame, const ThreeCardRois &rois,
                  const AugmentScreenDetectorConfig &config,
                  const std::int32_t offset_x, const std::int32_t offset_y) {
  DetectorResult result;
  result.frame_id = frame.frame_id;
  result.rois = rois;
  result.metrics.selected_offset_x = offset_x;
  result.metrics.selected_offset_y = offset_y;

  for (std::size_t index = 0U; index < rois.cards.size(); ++index) {
    const auto measured = MeasureCard(
        frame, rois.cards[index], config.sample_step,
        config.edge_delta_threshold, config.bright_border_luma_threshold);
    result.metrics.mean_luma[index] = measured.luma;
    result.metrics.edge_density[index] = measured.edges;
    result.metrics.surround_luma_contrast[index] =
        measured.surround_luma_contrast;
    result.metrics.bright_border_density[index] =
        measured.bright_border_density;
    result.metrics.border_luma_contrast[index] = measured.border_luma_contrast;
  }

  const float minimum_luma = *std::min_element(result.metrics.mean_luma.begin(),
                                               result.metrics.mean_luma.end());
  const float minimum_edges = *std::min_element(
      result.metrics.edge_density.begin(), result.metrics.edge_density.end());
  const float minimum_surround_contrast =
      *std::min_element(result.metrics.surround_luma_contrast.begin(),
                        result.metrics.surround_luma_contrast.end());
  const float minimum_bright_border =
      *std::min_element(result.metrics.bright_border_density.begin(),
                        result.metrics.bright_border_density.end());
  const float minimum_border_contrast =
      *std::min_element(result.metrics.border_luma_contrast.begin(),
                        result.metrics.border_luma_contrast.end());
  const float luma_spread = Spread(result.metrics.mean_luma);
  const float edge_spread = Spread(result.metrics.edge_density);
  const float surround_contrast_spread =
      Spread(result.metrics.surround_luma_contrast);
  const float bright_border_spread =
      Spread(result.metrics.bright_border_density);
  const float border_contrast_spread =
      Spread(result.metrics.border_luma_contrast);
  const float luma_consistency =
      config.maximum_luma_spread == 0.0F
          ? static_cast<float>(luma_spread == 0.0F)
          : Clamp01(1.0F - luma_spread / config.maximum_luma_spread);
  const float edge_consistency =
      config.maximum_edge_spread == 0.0F
          ? static_cast<float>(edge_spread == 0.0F)
          : Clamp01(1.0F - edge_spread / config.maximum_edge_spread);
  const float surround_consistency =
      config.maximum_surround_contrast_spread == 0.0F
          ? static_cast<float>(surround_contrast_spread == 0.0F)
          : Clamp01(1.0F - surround_contrast_spread /
                               config.maximum_surround_contrast_spread);
  const float luma_score = Clamp01(minimum_luma / config.minimum_card_luma);
  const float edge_score = Clamp01(minimum_edges / config.minimum_edge_density);
  const float surround_score = Clamp01(minimum_surround_contrast /
                                       config.minimum_surround_luma_contrast);
  const float bright_border_score =
      Clamp01(minimum_bright_border / config.minimum_bright_border_density);
  const float border_contrast_score =
      Clamp01(minimum_border_contrast / config.minimum_border_luma_contrast);
  // Tiny synthetic plumbing frames cannot represent the measured LoL frame
  // thickness. Preserve the Phase1 surround gate there; real/preview fixture
  // sizes use the calibrated four-sided frame model.
  const bool use_measured_frame_gate =
      frame.width >= 1280U && frame.height >= 720U;
  if (use_measured_frame_gate) {
    // A hovered card is intentionally brighter than its neighbours. Treat
    // three-column evidence as the conjunction that all three measured frames
    // are present, while retaining luma/edge similarity as softer evidence.
    // Surround contrast is diagnostic only: the outside pixels are live game
    // content and are not stable across maps or tooltip states.
    result.metrics.three_column_consistency =
        Clamp01(0.20F * LayoutConsistency(rois) + 0.15F * luma_consistency +
                0.15F * edge_consistency + 0.25F * bright_border_score +
                0.25F * border_contrast_score);
    result.confidence = Clamp01(
        0.15F * luma_score + 0.15F * edge_score + 0.05F * surround_score +
        0.20F * bright_border_score + 0.20F * border_contrast_score +
        0.25F * result.metrics.three_column_consistency);
  } else {
    result.metrics.three_column_consistency =
        std::min({LayoutConsistency(rois), luma_consistency, edge_consistency,
                  surround_consistency});
    result.confidence = Clamp01(
        0.25F * luma_score + 0.25F * edge_score + 0.25F * surround_score +
        0.25F * result.metrics.three_column_consistency);
  }

  if (minimum_luma < config.minimum_card_luma) {
    result.reason = "insufficient_luma";
  } else if (minimum_edges < config.minimum_edge_density) {
    result.reason = "insufficient_edges";
  } else if (!use_measured_frame_gate &&
             minimum_surround_contrast <
                 config.minimum_surround_luma_contrast) {
    result.reason = "insufficient_card_surround_contrast";
  } else if (use_measured_frame_gate &&
             minimum_bright_border < config.minimum_bright_border_density) {
    result.reason = "insufficient_bright_card_border";
  } else if (use_measured_frame_gate &&
             minimum_border_contrast < config.minimum_border_luma_contrast) {
    result.reason = "insufficient_card_border_contrast";
  } else if (luma_spread > config.maximum_luma_spread) {
    result.reason = "inconsistent_column_luma";
  } else if (edge_spread > config.maximum_edge_spread) {
    result.reason = "inconsistent_column_edges";
  } else if (!use_measured_frame_gate &&
             surround_contrast_spread >
                 config.maximum_surround_contrast_spread) {
    result.reason = "inconsistent_column_surround_contrast";
  } else if (use_measured_frame_gate &&
             bright_border_spread >
                 config.maximum_bright_border_density_spread) {
    result.reason = "inconsistent_column_bright_borders";
  } else if (use_measured_frame_gate &&
             border_contrast_spread >
                 config.maximum_border_luma_contrast_spread) {
    result.reason = "inconsistent_column_border_contrast";
  } else if (result.metrics.three_column_consistency <
             config.minimum_three_column_consistency) {
    result.reason = "inconsistent_three_column_geometry";
  } else if (result.confidence < config.visible_confidence_threshold) {
    result.reason = "below_visible_confidence";
  } else {
    result.visible = true;
    result.reason = "visible";
  }
  return result;
}

[[nodiscard]] std::uint32_t ScaledPixels(const std::uint32_t extent,
                                         const double ratio) noexcept {
  return static_cast<std::uint32_t>(std::clamp(
      std::llround(static_cast<double>(extent) * ratio), 0LL,
      static_cast<long long>(std::numeric_limits<std::uint32_t>::max())));
}

[[nodiscard]] std::vector<std::int32_t>
BuildOffsets(const std::uint32_t maximum, const std::uint32_t step) {
  std::vector<std::int32_t> offsets{0};
  if (maximum == 0U) {
    return offsets;
  }
  const std::uint32_t actual_step = std::max(1U, step);
  for (std::uint32_t magnitude = actual_step; magnitude <= maximum;) {
    offsets.push_back(-static_cast<std::int32_t>(magnitude));
    offsets.push_back(static_cast<std::int32_t>(magnitude));
    if (maximum - magnitude < actual_step) {
      break;
    }
    magnitude += actual_step;
  }
  if (maximum % actual_step != 0U) {
    offsets.push_back(-static_cast<std::int32_t>(maximum));
    offsets.push_back(static_cast<std::int32_t>(maximum));
  }
  return offsets;
}

[[nodiscard]] bool IsBetterCandidate(const DetectorResult &candidate,
                                     const DetectorResult &current) noexcept {
  if (candidate.visible != current.visible) {
    return candidate.visible;
  }
  const auto candidate_distance =
      std::abs(static_cast<std::int64_t>(candidate.metrics.selected_offset_x)) +
      std::abs(static_cast<std::int64_t>(candidate.metrics.selected_offset_y));
  const auto current_distance =
      std::abs(static_cast<std::int64_t>(current.metrics.selected_offset_x)) +
      std::abs(static_cast<std::int64_t>(current.metrics.selected_offset_y));
  // Once the manually measured seed satisfies every hard gate, animation is
  // not evidence that OCR geometry moved. Search remains available when the
  // seed itself is rejected.
  const bool measured_frame_scale = current.rois.has_value() &&
                                    current.rois->resolution.width >= 1280U &&
                                    current.rois->resolution.height >= 720U;
  if (measured_frame_scale && candidate.visible && current.visible) {
    if (current_distance == 0) {
      return false;
    }
    if (candidate_distance == 0) {
      return true;
    }
  }
  // The Phase2 seed is manually calibrated. Small score fluctuations from
  // animation must not move a 23 px title crop several pixels off truth.
  // Require a material improvement before accepting a farther candidate.
  constexpr float epsilon = 0.015F;
  if (candidate.confidence > current.confidence + epsilon) {
    return true;
  }
  if (current.confidence > candidate.confidence + epsilon) {
    return false;
  }
  const float candidate_surround =
      *std::min_element(candidate.metrics.surround_luma_contrast.begin(),
                        candidate.metrics.surround_luma_contrast.end());
  const float current_surround =
      *std::min_element(current.metrics.surround_luma_contrast.begin(),
                        current.metrics.surround_luma_contrast.end());
  if (candidate_surround > current_surround + epsilon) {
    return true;
  }
  if (current_surround > candidate_surround + epsilon) {
    return false;
  }
  if (candidate_distance != current_distance) {
    return candidate_distance < current_distance;
  }
  if (candidate.metrics.selected_offset_y !=
      current.metrics.selected_offset_y) {
    return candidate.metrics.selected_offset_y <
           current.metrics.selected_offset_y;
  }
  return candidate.metrics.selected_offset_x <
         current.metrics.selected_offset_x;
}

} // namespace

bool RoiSearchConfig::IsValid() const noexcept {
  if (!BoundedRatio(maximum_horizontal_offset_ratio) ||
      !BoundedRatio(maximum_vertical_offset_ratio) ||
      !std::isfinite(offset_step_ratio) || offset_step_ratio <= 0.0 ||
      offset_step_ratio > 0.25) {
    return false;
  }
  constexpr double maximum_steps_per_axis = 16.0;
  return !enabled || (maximum_horizontal_offset_ratio / offset_step_ratio <=
                          maximum_steps_per_axis &&
                      maximum_vertical_offset_ratio / offset_step_ratio <=
                          maximum_steps_per_axis);
}

bool AugmentScreenDetectorConfig::IsValid() const noexcept {
  constexpr std::uint32_t kMaximumSafeSampleStep = 4'096U;
  return layout.IsValid() && roi_search.IsValid() && sample_step > 0U &&
         sample_step <= kMaximumSafeSampleStep && edge_delta_threshold > 0U &&
         bright_border_luma_threshold > 0U &&
         std::isfinite(aspect_ratio_tolerance) &&
         aspect_ratio_tolerance >= 0.0 &&
         (!ui_scale.has_value() ||
          (std::isfinite(*ui_scale) && *ui_scale > 0.0)) &&
         UnitFloat(minimum_card_luma) && minimum_card_luma > 0.0F &&
         UnitFloat(minimum_edge_density) && minimum_edge_density > 0.0F &&
         UnitFloat(minimum_surround_luma_contrast) &&
         minimum_surround_luma_contrast > 0.0F &&
         UnitFloat(minimum_bright_border_density) &&
         minimum_bright_border_density > 0.0F &&
         UnitFloat(minimum_border_luma_contrast) &&
         minimum_border_luma_contrast > 0.0F &&
         UnitFloat(maximum_luma_spread) && UnitFloat(maximum_edge_spread) &&
         UnitFloat(maximum_surround_contrast_spread) &&
         UnitFloat(maximum_bright_border_density_spread) &&
         UnitFloat(maximum_border_luma_contrast_spread) &&
         UnitFloat(minimum_three_column_consistency) &&
         UnitFloat(visible_confidence_threshold);
}

AugmentScreenDetector::AugmentScreenDetector(AugmentScreenDetectorConfig config)
    : config_(std::move(config)) {
  if (!config_.IsValid()) {
    throw std::invalid_argument("invalid augment screen detector config");
  }
}

const AugmentScreenDetectorConfig &
AugmentScreenDetector::config() const noexcept {
  return config_;
}

DetectorResult AugmentScreenDetector::Detect(const common::Frame &frame) const {
  DetectorResult rejected;
  rejected.frame_id = frame.frame_id;
  if (!frame.IsValid()) {
    rejected.reason = "invalid_frame";
    return rejected;
  }
  const auto roi_result =
      ComputeThreeCardRoiCandidates(frame.width, frame.height, config_.layout,
                                    config_.aspect_ratio_tolerance);
  if (!roi_result.ok()) {
    rejected.reason = roi_result.reason;
    return rejected;
  }

  std::vector<std::int32_t> offsets_x{0};
  std::vector<std::int32_t> offsets_y{0};
  if (config_.roi_search.enabled) {
    offsets_x = BuildOffsets(
        ScaledPixels(frame.width,
                     config_.roi_search.maximum_horizontal_offset_ratio),
        ScaledPixels(frame.width, config_.roi_search.offset_step_ratio));
    offsets_y = BuildOffsets(
        ScaledPixels(frame.height,
                     config_.roi_search.maximum_vertical_offset_ratio),
        ScaledPixels(frame.height, config_.roi_search.offset_step_ratio));
  }

  std::optional<DetectorResult> best_failure;
  for (auto seed : roi_result.values) {
    seed.ui_scale = config_.ui_scale;
    if (!seed.IsValid()) {
      continue;
    }
    std::optional<DetectorResult> best_for_seed;
    std::uint32_t evaluated_candidates = 0U;
    for (const auto offset_y : offsets_y) {
      for (const auto offset_x : offsets_x) {
        const auto candidate_rois =
            OffsetThreeCardRois(seed, offset_x, offset_y);
        if (!candidate_rois.has_value()) {
          continue;
        }
        auto candidate = EvaluateCandidate(frame, *candidate_rois, config_,
                                           offset_x, offset_y);
        ++evaluated_candidates;
        if (!best_for_seed.has_value() ||
            IsBetterCandidate(candidate, *best_for_seed)) {
          best_for_seed = std::move(candidate);
        }
      }
    }
    if (!best_for_seed.has_value()) {
      continue;
    }
    best_for_seed->metrics.evaluated_candidates = evaluated_candidates;
    // Preserve the already-validated Phase2 seed whenever it passes.  Only
    // consult compact/window variants after an earlier layout has failed.
    if (best_for_seed->visible) {
      return *best_for_seed;
    }
    if (!best_failure.has_value() ||
        IsBetterCandidate(*best_for_seed, *best_failure)) {
      best_failure = std::move(best_for_seed);
    }
  }
  if (!best_failure.has_value()) {
    rejected.reason = "no_valid_roi_search_candidate";
    return rejected;
  }
  return *best_failure;
}

bool StableDetectorConfig::IsValid() const noexcept {
  return required_consecutive_frames > 0U &&
         UnitFloat(minimum_frame_confidence);
}

ConsecutiveFrameConfirmer::ConsecutiveFrameConfirmer(
    StableDetectorConfig config)
    : config_(config) {
  if (!config_.IsValid()) {
    throw std::invalid_argument("invalid consecutive frame confirmer config");
  }
}

DetectorResult
ConsecutiveFrameConfirmer::Observe(const DetectorResult &current) {
  DetectorResult stable = current;
  // WGC frame ids belong to the capture producer, while the detector consumes
  // a throttled latest-frame stream. Dropped producer frames are expected and
  // must not reset visual stability; only duplicate or out-of-order
  // observations break the consecutive processed-observation window.
  const bool contiguous =
      !previous_frame_id_.has_value() ||
      (*previous_frame_id_ != std::numeric_limits<std::uint64_t>::max() &&
       current.frame_id > *previous_frame_id_);
  if (!current.visible ||
      current.confidence < config_.minimum_frame_confidence ||
      !current.rois.has_value() || !contiguous) {
    Reset();
    previous_frame_id_ = current.frame_id;
    stable.visible = false;
    if (current.visible && !contiguous) {
      stable.reason = "non_contiguous_frame";
    }
    return stable;
  }

  previous_frame_id_ = current.frame_id;
  ++consecutive_visible_frames_;
  window_confidence_ =
      std::min(window_confidence_, std::clamp(current.confidence, 0.0F, 1.0F));
  if (consecutive_visible_frames_ < config_.required_consecutive_frames) {
    stable.visible = false;
    stable.confidence = window_confidence_;
    stable.reason =
        "awaiting_stability:" + std::to_string(consecutive_visible_frames_) +
        "/" + std::to_string(config_.required_consecutive_frames);
    return stable;
  }
  stable.visible = true;
  stable.confidence = window_confidence_;
  stable.reason = "stable_visible";
  return stable;
}

void ConsecutiveFrameConfirmer::Reset() noexcept {
  consecutive_visible_frames_ = 0U;
  previous_frame_id_.reset();
  window_confidence_ = 1.0F;
}

std::uint32_t
ConsecutiveFrameConfirmer::consecutive_visible_frames() const noexcept {
  return consecutive_visible_frames_;
}

} // namespace lol_assistant::detector
