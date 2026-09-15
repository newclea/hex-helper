#include "lol_assistant/capture/window_info.h"

#include <cwctype>
#include <limits>
#include <utility>

namespace lol_assistant::capture {
namespace {

[[nodiscard]] bool HasVisibleText(const std::wstring_view title) noexcept {
  for (const wchar_t character : title) {
    if (std::iswspace(character) == 0) {
      return true;
    }
  }
  return false;
}

[[nodiscard]] std::wstring ReadWindowTitle(const HWND window) {
  const int length = GetWindowTextLengthW(window);
  if (length <= 0) {
    return {};
  }

  std::wstring title(static_cast<std::size_t>(length) + 1U, L'\0');
  const int copied = GetWindowTextW(window, title.data(), length + 1);
  if (copied <= 0) {
    return {};
  }
  title.resize(static_cast<std::size_t>(copied));
  return title;
}

BOOL CALLBACK CollectWindow(const HWND window, const LPARAM parameter) {
  auto* const windows = reinterpret_cast<std::vector<WindowInfo>*>(parameter);
  if (windows == nullptr || window == nullptr || IsWindow(window) == FALSE ||
      IsWindowVisible(window) == FALSE) {
    return TRUE;
  }

  std::wstring title = ReadWindowTitle(window);
  if (!HasVisibleText(title)) {
    return TRUE;
  }

  DWORD process_id = 0U;
  static_cast<void>(GetWindowThreadProcessId(window, &process_id));
  if (process_id == 0U) {
    return TRUE;
  }

  windows->push_back(WindowInfo{window, std::move(title), process_id});
  return TRUE;
}

[[nodiscard]] bool CaseInsensitiveContains(const std::wstring_view value,
                                           const std::wstring_view query) {
  constexpr auto maximum_length =
      static_cast<std::size_t>(std::numeric_limits<int>::max());
  if (query.empty() || query.size() > value.size() ||
      value.size() > maximum_length) {
    return false;
  }

  for (std::size_t offset = 0U; offset + query.size() <= value.size();
       ++offset) {
    if (CompareStringOrdinal(value.data() + offset,
                             static_cast<int>(query.size()), query.data(),
                             static_cast<int>(query.size()), TRUE) ==
        CSTR_EQUAL) {
      return true;
    }
  }
  return false;
}

}  // namespace

bool WindowInfo::IsValid() const noexcept {
  return handle != nullptr && IsWindow(handle) != FALSE && process_id != 0U &&
         HasVisibleText(title);
}

std::vector<WindowInfo> EnumerateTopLevelWindows() {
  std::vector<WindowInfo> windows;
  static_cast<void>(EnumWindows(&CollectWindow,
                                reinterpret_cast<LPARAM>(&windows)));
  return windows;
}

std::optional<WindowInfo> FindTopLevelWindow(
    const std::wstring_view query, const WindowTitleMatchMode mode) {
  if (!HasVisibleText(query)) {
    return std::nullopt;
  }

  for (auto& window : EnumerateTopLevelWindows()) {
    bool matches = false;
    switch (mode) {
      case WindowTitleMatchMode::Exact:
        matches = window.title == query;
        break;
      case WindowTitleMatchMode::CaseInsensitiveSubstring:
        matches = CaseInsensitiveContains(window.title, query);
        break;
      default:
        return std::nullopt;
    }
    if (matches) {
      return std::move(window);
    }
  }
  return std::nullopt;
}

}  // namespace lol_assistant::capture
