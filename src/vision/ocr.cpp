#include "lol_assistant/vision/ocr.h"

#include <roapi.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Globalization.h>
#include <winrt/Windows.Media.Ocr.h>
#include <winrt/base.h>

#include <algorithm>
#include <iomanip>
#include <optional>
#include <sstream>
#include <string_view>
#include <utility>

#include "lol_assistant/vision/software_bitmap.h"

namespace lol_assistant::vision {
namespace {

class ApartmentScope final {
 public:
  ApartmentScope() noexcept : result_(RoInitialize(RO_INIT_MULTITHREADED)) {}
  ~ApartmentScope() {
    if (result_ == S_OK || result_ == S_FALSE) {
      RoUninitialize();
    }
  }

  ApartmentScope(const ApartmentScope&) = delete;
  ApartmentScope& operator=(const ApartmentScope&) = delete;

  [[nodiscard]] bool available() const noexcept {
    return SUCCEEDED(result_) || result_ == RPC_E_CHANGED_MODE;
  }
  [[nodiscard]] HRESULT result() const noexcept { return result_; }

 private:
  HRESULT result_{E_FAIL};
};

[[nodiscard]] std::string HresultReason(const std::string_view prefix,
                                        const HRESULT result) {
  std::ostringstream stream;
  stream << prefix << ":0x" << std::hex << std::uppercase
         << static_cast<std::uint32_t>(result);
  return stream.str();
}

[[nodiscard]] OcrBackendStatus ProbeInCurrentApartment() noexcept {
  try {
    const auto factory = winrt::try_get_activation_factory<
        winrt::Windows::Media::Ocr::OcrEngine,
        winrt::Windows::Media::Ocr::IOcrEngineStatics>();
    if (!factory) {
      return {OcrBackendState::BackendUnavailable, kWindowsMediaOcrZhCnBackend,
              "backend_unavailable:ocr_activation_factory_unavailable"};
    }
    std::optional<winrt::Windows::Globalization::Language> selected;
    for (const auto& language : factory.AvailableRecognizerLanguages()) {
      const auto tag = language.LanguageTag();
      if (CompareStringOrdinal(tag.c_str(), static_cast<int>(tag.size()),
                               L"zh-CN", -1, TRUE) == CSTR_EQUAL ||
          CompareStringOrdinal(tag.c_str(), static_cast<int>(tag.size()),
                               L"zh-Hans-CN", -1, TRUE) == CSTR_EQUAL) {
        selected = language;
        break;
      }
    }
    if (!selected.has_value()) {
      return {OcrBackendState::BackendUnavailable, kWindowsMediaOcrZhCnBackend,
              "backend_unavailable:zh-CN_language_not_installed"};
    }
    const auto engine = factory.TryCreateFromLanguage(*selected);
    if (!engine) {
      return {OcrBackendState::BackendUnavailable, kWindowsMediaOcrZhCnBackend,
              "backend_unavailable:engine_creation_returned_null"};
    }
    return {OcrBackendState::Available, kWindowsMediaOcrZhCnBackend,
            "available"};
  } catch (const winrt::hresult_error& error) {
    return {OcrBackendState::BackendUnavailable, kWindowsMediaOcrZhCnBackend,
            HresultReason("backend_unavailable:winrt_error", error.code()) +
                ":" + winrt::to_string(error.message())};
  } catch (...) {
    return {OcrBackendState::BackendUnavailable, kWindowsMediaOcrZhCnBackend,
            "backend_unavailable:unknown_exception"};
  }
}

[[nodiscard]] winrt::Windows::Media::Ocr::OcrEngine
CachedZhCnEngine(
    const winrt::Windows::Media::Ocr::IOcrEngineStatics& factory) {
  struct EngineCache final {
    winrt::Windows::Media::Ocr::OcrEngine engine{nullptr};
  };
  thread_local EngineCache cache;
  if (cache.engine) {
    return cache.engine;
  }
  std::optional<winrt::Windows::Globalization::Language> selected;
  for (const auto& language : factory.AvailableRecognizerLanguages()) {
    const auto tag = language.LanguageTag();
    if (CompareStringOrdinal(tag.c_str(), static_cast<int>(tag.size()),
                             L"zh-CN", -1, TRUE) == CSTR_EQUAL ||
        CompareStringOrdinal(tag.c_str(), static_cast<int>(tag.size()),
                             L"zh-Hans-CN", -1, TRUE) == CSTR_EQUAL) {
      selected = language;
      break;
    }
  }
  if (!selected.has_value()) {
    return nullptr;
  }
  cache.engine = factory.TryCreateFromLanguage(*selected);
  return cache.engine;
}

}  // namespace

OcrBackendStatus WindowsMediaOcrTitleRecognizer::Probe() const noexcept {
  const ApartmentScope apartment;
  if (!apartment.available()) {
    return {OcrBackendState::BackendUnavailable, kWindowsMediaOcrZhCnBackend,
            HresultReason("backend_unavailable:ro_initialize_failed",
                          apartment.result())};
  }
  return ProbeInCurrentApartment();
}

OcrTextResult WindowsMediaOcrTitleRecognizer::Recognize(
    const detector::OwningBgraCrop& crop) const noexcept {
  try {
    return RecognizeCore(crop);
  } catch (const winrt::hresult_error& error) {
    return {OcrResultState::RecognitionFailed,
            {},
            kWindowsMediaOcrZhCnBackend,
            std::nullopt,
            HresultReason("recognition_failed", error.code()) + ":" +
                winrt::to_string(error.message())};
  } catch (...) {
    return {OcrResultState::RecognitionFailed,
            {},
            kWindowsMediaOcrZhCnBackend,
            std::nullopt,
            "recognition_failed:unknown_exception"};
  }
}

OcrTextResult WindowsMediaOcrTitleRecognizer::RecognizeCore(
    const detector::OwningBgraCrop& crop) const {
  const ApartmentScope apartment;
  if (!apartment.available()) {
    return {OcrResultState::BackendUnavailable,
            {},
            kWindowsMediaOcrZhCnBackend,
            std::nullopt,
            HresultReason("backend_unavailable:ro_initialize_failed",
                          apartment.result())};
  }
  if (!crop.IsValid()) {
    return {OcrResultState::InvalidInput,
            {},
            kWindowsMediaOcrZhCnBackend,
            std::nullopt,
            "invalid_bgra_crop"};
  }
  const auto factory = winrt::try_get_activation_factory<
      winrt::Windows::Media::Ocr::OcrEngine,
      winrt::Windows::Media::Ocr::IOcrEngineStatics>();
  if (!factory) {
    return {OcrResultState::BackendUnavailable,
            {},
            kWindowsMediaOcrZhCnBackend,
            std::nullopt,
            "backend_unavailable:ocr_activation_factory_unavailable"};
  }
  const auto maximum_dimension = factory.MaxImageDimension();
  if (crop.width > maximum_dimension || crop.height > maximum_dimension) {
    return {OcrResultState::InvalidInput,
            {},
            kWindowsMediaOcrZhCnBackend,
            std::nullopt,
            "ocr_image_exceeds_maximum_dimension"};
  }

  const auto bitmap = BgraCropToSoftwareBitmap(crop);
  if (!bitmap.ok()) {
    return {OcrResultState::InvalidInput,
            {},
            kWindowsMediaOcrZhCnBackend,
            std::nullopt,
            bitmap.reason};
  }
  const auto engine = CachedZhCnEngine(factory);
  if (!engine) {
    const auto backend = ProbeInCurrentApartment();
    return {OcrResultState::BackendUnavailable,
            {},
            backend.backend,
            std::nullopt,
            backend.available()
                ? "backend_unavailable:engine_creation_returned_null"
                : backend.reason};
  }
  const auto result = engine.RecognizeAsync(*bitmap.bitmap).get();
  std::vector<std::string> line_candidates;
  std::vector<OcrLineSpan> line_spans;
  line_candidates.reserve(kMaximumOcrLineCandidates);
  line_spans.reserve(kMaximumOcrLineSpans);
  const auto contains_cjk = [](const std::string_view text) noexcept {
    for (std::size_t index = 0U; index < text.size(); ++index) {
      const auto value = static_cast<unsigned char>(text[index]);
      if (value >= 0xE4U && value <= 0xE9U) {
        return true;
      }
    }
    return false;
  };
  const auto append_span = [&line_spans, &contains_cjk](
                               std::string text, const float x, const float y,
                               const float width, const float height) {
    if (text.empty() || text.size() > kMaximumOcrLineCandidateBytes ||
        line_spans.size() >= kMaximumOcrLineSpans || !contains_cjk(text)) {
      return;
    }
    line_spans.push_back(
        {std::move(text), x, y, std::max(width, 1.0F), std::max(height, 1.0F)});
  };
  for (const auto& line : result.Lines()) {
    auto line_text = winrt::to_string(line.Text());
    if (!line_text.empty() && line_text.size() <= kMaximumOcrLineCandidateBytes &&
        line_candidates.size() < kMaximumOcrLineCandidates) {
      line_candidates.push_back(line_text);
    }
    struct WordBox final {
      std::string text{};
      float x{0.0F};
      float y{0.0F};
      float right{0.0F};
      float bottom{0.0F};
    };
    std::vector<WordBox> words;
    for (const auto& word : line.Words()) {
      auto text = winrt::to_string(word.Text());
      if (text.empty()) {
        continue;
      }
      const auto rect = word.BoundingRect();
      words.push_back({std::move(text), static_cast<float>(rect.X),
                       static_cast<float>(rect.Y),
                       static_cast<float>(rect.X + rect.Width),
                       static_cast<float>(rect.Y + rect.Height)});
    }
    if (words.empty()) {
      if (!line_text.empty()) {
        append_span(std::move(line_text), 0.0F, 0.0F, 1.0F, 1.0F);
      }
      continue;
    }
    append_span(line_text, words.front().x, words.front().y,
                words.back().right - words.front().x,
                words.back().bottom - words.front().y);
    for (std::size_t start = 0U; start < words.size(); ++start) {
      std::string joined;
      float left = words[start].x;
      float top = words[start].y;
      float right = words[start].right;
      float bottom = words[start].bottom;
      for (std::size_t end = start; end < words.size(); ++end) {
        if (end > start) {
          const float gap = words[end].x - right;
          const float run_width = std::max(right - left, 1.0F);
          const float max_gap =
              std::max(8.0F, run_width / static_cast<float>(end - start + 1U));
          if (gap > max_gap * 1.75F) {
            break;
          }
        }
        joined.append(words[end].text);
        left = std::min(left, words[end].x);
        top = std::min(top, words[end].y);
        right = std::max(right, words[end].right);
        bottom = std::max(bottom, words[end].bottom);
        append_span(joined, left, top, right - left, bottom - top);
      }
    }
  }
  return {OcrResultState::Success,
          winrt::to_string(result.Text()),
          kWindowsMediaOcrZhCnBackend,
          std::nullopt,
          "recognized_without_backend_confidence",
          std::move(line_candidates),
          std::move(line_spans)};
}

}  // namespace lol_assistant::vision
