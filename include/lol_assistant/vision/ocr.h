#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "lol_assistant/detector/roi.h"

namespace lol_assistant::vision {

inline constexpr char kWindowsMediaOcrZhCnBackend[] = "windows_media_ocr:zh-CN";
inline constexpr std::size_t kMaximumOcrLineCandidates = 48U;
inline constexpr std::size_t kMaximumOcrLineCandidateBytes = 512U;
inline constexpr std::size_t kMaximumOcrLineSpans = 1024U;

struct OcrLineSpan final {
  std::string text{};
  float x{0.0F};
  float y{0.0F};
  float width{0.0F};
  float height{0.0F};

  [[nodiscard]] float center_x() const noexcept { return x + width * 0.5F; }
  [[nodiscard]] float center_y() const noexcept { return y + height * 0.5F; }
};

enum class OcrBackendState : std::uint8_t {
  Available = 0,
  BackendUnavailable = 1,
};

struct OcrBackendStatus final {
  OcrBackendState state{OcrBackendState::BackendUnavailable};
  std::string backend{kWindowsMediaOcrZhCnBackend};
  std::string reason{"backend_unavailable:not_probed"};

  [[nodiscard]] bool available() const noexcept {
    return state == OcrBackendState::Available;
  }
};

enum class OcrResultState : std::uint8_t {
  Success = 0,
  BackendUnavailable = 1,
  InvalidInput = 2,
  RecognitionFailed = 3,
};

struct OcrTextResult final {
  OcrResultState state{OcrResultState::RecognitionFailed};
  std::string raw_text{};
  std::string backend{kWindowsMediaOcrZhCnBackend};
  // Windows.Media.Ocr does not expose a confidence value. nullopt is the
  // truthful representation and must not be replaced with a synthetic score.
  std::optional<float> ocr_confidence{};
  std::string reason{"not_recognized"};
  // Bounded OCR lines supplement raw_text; raw_text remains the unchanged
  // aggregate for diagnostics and existing consumers.
  std::vector<std::string> line_candidates{};
  std::vector<OcrLineSpan> line_spans{};

  [[nodiscard]] bool ok() const noexcept {
    return state == OcrResultState::Success;
  }
};

class IOcrTitleRecognizer {
 public:
  virtual ~IOcrTitleRecognizer() = default;

  [[nodiscard]] virtual OcrBackendStatus Probe() const noexcept = 0;
  [[nodiscard]] virtual OcrTextResult Recognize(
      const detector::OwningBgraCrop& crop) const noexcept = 0;
};

class WindowsMediaOcrTitleRecognizer : public IOcrTitleRecognizer {
 public:
  [[nodiscard]] OcrBackendStatus Probe() const noexcept override;
  [[nodiscard]] OcrTextResult Recognize(
      const detector::OwningBgraCrop& crop) const noexcept override;

 protected:
  // The noexcept wrapper owns the exception boundary. Keeping the WinRT core
  // separately overridable makes that boundary deterministically testable.
  [[nodiscard]] virtual OcrTextResult RecognizeCore(
      const detector::OwningBgraCrop& crop) const;
};

}  // namespace lol_assistant::vision
