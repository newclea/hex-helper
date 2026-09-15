#pragma once

#include <windows.h>

#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace lol_assistant::capture {

struct WindowInfo final {
  HWND handle{nullptr};
  std::wstring title{};
  DWORD process_id{0U};

  [[nodiscard]] bool IsValid() const noexcept;
};

enum class WindowTitleMatchMode {
  Exact,
  CaseInsensitiveSubstring,
};

[[nodiscard]] std::vector<WindowInfo> EnumerateTopLevelWindows();

[[nodiscard]] std::optional<WindowInfo> FindTopLevelWindow(
    std::wstring_view query, WindowTitleMatchMode mode);

}  // namespace lol_assistant::capture
