#include "lol_assistant/output/debug_preview_window.h"

#include <Windows.h>

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

namespace lol_assistant::output {
namespace {

constexpr wchar_t kPreviewWindowClass[] =
    L"LolAssistantReplayDebugPreviewWindow";

[[nodiscard]] std::wstring Utf8ToWide(const std::string& input) {
  if (input.empty()) {
    return {};
  }
  int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                                  static_cast<int>(input.size()), nullptr, 0);
  DWORD flags = MB_ERR_INVALID_CHARS;
  if (count == 0) {
    flags = 0U;
    count = MultiByteToWideChar(CP_UTF8, flags, input.data(),
                                static_cast<int>(input.size()), nullptr, 0);
  }
  if (count <= 0) {
    return L"Invalid UTF-8 status";
  }
  std::wstring output(static_cast<std::size_t>(count), L'\0');
  if (MultiByteToWideChar(CP_UTF8, flags, input.data(),
                          static_cast<int>(input.size()), output.data(),
                          count) == 0) {
    return L"Invalid UTF-8 status";
  }
  return output;
}

[[nodiscard]] RECT RoiToRect(const common::NormalizedRoi& roi,
                             const RECT& client) {
  const int width = client.right - client.left;
  const int height = client.bottom - client.top;
  RECT result{};
  result.left = client.left +
                static_cast<int>(std::lround(roi.x * width));
  result.top = client.top +
               static_cast<int>(std::lround(roi.y * height));
  result.right = client.left +
                 static_cast<int>(std::lround((roi.x + roi.width) * width));
  result.bottom = client.top + static_cast<int>(
                                   std::lround((roi.y + roi.height) * height));
  return result;
}

void DrawRoi(HDC device_context, const RECT& rectangle, const COLORREF color,
             const int thickness) {
  const HPEN pen = CreatePen(PS_SOLID, thickness, color);
  if (pen == nullptr) {
    return;
  }
  const HGDIOBJ previous_pen = SelectObject(device_context, pen);
  const HGDIOBJ previous_brush =
      SelectObject(device_context, GetStockObject(HOLLOW_BRUSH));
  Rectangle(device_context, rectangle.left, rectangle.top, rectangle.right,
            rectangle.bottom);
  SelectObject(device_context, previous_brush);
  SelectObject(device_context, previous_pen);
  DeleteObject(pen);
}

}  // namespace

class DebugPreviewWindow::Impl final {
 public:
  Impl(std::string title, const int client_width, const int client_height)
      : title_(Utf8ToWide(title)),
        client_width_(client_width),
        client_height_(client_height) {
    if (title_.empty()) {
      title_ = L"LoL Assistant Debug Preview";
    }
    if (client_width_ <= 0 || client_height_ <= 0) {
      throw std::invalid_argument(
          "Debug preview client dimensions must be positive");
    }
  }

  [[nodiscard]] bool Create() {
    if (window_ != nullptr) {
      return true;
    }
    instance_ = GetModuleHandleW(nullptr);
    if (instance_ == nullptr) {
      return false;
    }

    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.style = CS_HREDRAW | CS_VREDRAW;
    window_class.lpfnWndProc = &Impl::WindowProcedure;
    window_class.hInstance = instance_;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hbrBackground =
        reinterpret_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
    window_class.lpszClassName = kPreviewWindowClass;
    if (RegisterClassExW(&window_class) == 0U &&
        GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
      return false;
    }

    constexpr DWORD extended_style = WS_EX_NOACTIVATE | WS_EX_APPWINDOW;
    constexpr DWORD window_style = WS_OVERLAPPEDWINDOW;
    RECT dimensions{0, 0, client_width_, client_height_};
    if (AdjustWindowRectEx(&dimensions, window_style, FALSE,
                           extended_style) == FALSE) {
      return false;
    }

    window_ = CreateWindowExW(
        extended_style, kPreviewWindowClass, title_.c_str(), window_style,
        CW_USEDEFAULT, CW_USEDEFAULT, dimensions.right - dimensions.left,
        dimensions.bottom - dimensions.top, nullptr, nullptr, instance_, this);
    if (window_ == nullptr) {
      return false;
    }
    ShowWindow(window_, SW_SHOWNOACTIVATE);
    UpdateWindow(window_);
    return true;
  }

  [[nodiscard]] bool IsOpen() const noexcept { return window_ != nullptr; }

  void UpdateFrame(const common::Frame& frame,
                   const DebugPreviewOverlay& overlay) {
    if (!frame.IsValid()) {
      throw std::invalid_argument("Debug preview requires a valid BGRA frame");
    }
    if (!std::isfinite(overlay.fps) || overlay.fps < 0.0) {
      throw std::invalid_argument("Debug preview FPS must be finite and >= 0");
    }
    if (overlay.detector_roi.has_value() &&
        !overlay.detector_roi->IsValid()) {
      throw std::invalid_argument("Debug preview detector ROI is invalid");
    }
    for (const auto& slot : overlay.card_rois) {
      if (slot.has_value() && !slot->IsValid()) {
        throw std::invalid_argument("Debug preview card ROI is invalid");
      }
    }
    frame_ = frame;
    overlay_ = overlay;
    if (window_ != nullptr) {
      InvalidateRect(window_, nullptr, FALSE);
    }
  }

  [[nodiscard]] bool PumpMessages() {
    MSG message{};
    while (PeekMessageW(&message, nullptr, 0U, 0U, PM_REMOVE) != FALSE) {
      TranslateMessage(&message);
      DispatchMessageW(&message);
    }
    return IsOpen();
  }

  void Close() noexcept {
    if (window_ != nullptr) {
      DestroyWindow(window_);
    }
  }

 private:
  static LRESULT CALLBACK WindowProcedure(HWND window, const UINT message,
                                          const WPARAM w_param,
                                          const LPARAM l_param) {
    Impl* self = reinterpret_cast<Impl*>(
        GetWindowLongPtrW(window, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
      const auto* create = reinterpret_cast<const CREATESTRUCTW*>(l_param);
      self = static_cast<Impl*>(create->lpCreateParams);
      SetWindowLongPtrW(window, GWLP_USERDATA,
                        reinterpret_cast<LONG_PTR>(self));
      if (self != nullptr) {
        self->window_ = window;
      }
    }

    switch (message) {
      case WM_ERASEBKGND:
        return 1;
      case WM_PAINT:
        if (self != nullptr) {
          self->Paint();
          return 0;
        }
        break;
      case WM_CLOSE:
        DestroyWindow(window);
        return 0;
      case WM_DESTROY:
        if (self != nullptr) {
          self->window_ = nullptr;
        }
        return 0;
      default:
        break;
    }
    return DefWindowProcW(window, message, w_param, l_param);
  }

  void Paint() const {
    PAINTSTRUCT paint{};
    const HDC device_context = BeginPaint(window_, &paint);
    if (device_context == nullptr) {
      return;
    }
    RECT client{};
    GetClientRect(window_, &client);
    FillRect(device_context, &client,
             reinterpret_cast<HBRUSH>(GetStockObject(BLACK_BRUSH)));

    if (frame_.has_value()) {
      BITMAPINFO bitmap_info{};
      bitmap_info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
      bitmap_info.bmiHeader.biWidth = static_cast<LONG>(frame_->width);
      bitmap_info.bmiHeader.biHeight = -static_cast<LONG>(frame_->height);
      bitmap_info.bmiHeader.biPlanes = 1U;
      bitmap_info.bmiHeader.biBitCount = 32U;
      bitmap_info.bmiHeader.biCompression = BI_RGB;
      SetStretchBltMode(device_context, HALFTONE);
      StretchDIBits(device_context, client.left, client.top,
                    client.right - client.left, client.bottom - client.top, 0,
                    0, static_cast<int>(frame_->width),
                    static_cast<int>(frame_->height), frame_->buffer.data(),
                    &bitmap_info, DIB_RGB_COLORS, SRCCOPY);

      if (overlay_.detector_roi.has_value()) {
        DrawRoi(device_context, RoiToRect(*overlay_.detector_roi, client),
                RGB(255, 220, 0), 3);
      }
      constexpr std::array<COLORREF, common::kAugmentCardCount> colors{
          RGB(0, 220, 255), RGB(80, 255, 80), RGB(255, 100, 180)};
      for (std::size_t index = 0U; index < overlay_.card_rois.size(); ++index) {
        if (overlay_.card_rois[index].has_value()) {
          DrawRoi(device_context,
                  RoiToRect(overlay_.card_rois[index]->roi, client),
                  colors[index], 2);
        }
      }
    }

    std::wostringstream status;
    status << std::fixed << std::setprecision(1) << L"FPS " << overlay_.fps;
    if (!overlay_.status.empty()) {
      status << L" | " << Utf8ToWide(overlay_.status);
    }
    RECT text_area = client;
    text_area.left += 10;
    text_area.top += 8;
    SetBkMode(device_context, TRANSPARENT);
    SetTextColor(device_context, RGB(255, 255, 255));
    const std::wstring text = status.str();
    DrawTextW(device_context, text.c_str(), static_cast<int>(text.size()),
              &text_area, DT_LEFT | DT_TOP | DT_SINGLELINE | DT_NOPREFIX);
    EndPaint(window_, &paint);
  }

  HINSTANCE instance_{nullptr};
  HWND window_{nullptr};
  std::wstring title_{};
  int client_width_{1280};
  int client_height_{720};
  std::optional<common::Frame> frame_{};
  DebugPreviewOverlay overlay_{};
};

DebugPreviewWindow::DebugPreviewWindow(std::string title,
                                       const int client_width,
                                       const int client_height)
    : impl_(std::make_unique<Impl>(std::move(title), client_width,
                                  client_height)) {}

DebugPreviewWindow::~DebugPreviewWindow() { Close(); }

bool DebugPreviewWindow::Create() { return impl_->Create(); }

bool DebugPreviewWindow::IsOpen() const noexcept { return impl_->IsOpen(); }

void DebugPreviewWindow::UpdateFrame(const common::Frame& frame,
                                     const DebugPreviewOverlay& overlay) {
  impl_->UpdateFrame(frame, overlay);
}

bool DebugPreviewWindow::PumpMessages() { return impl_->PumpMessages(); }

void DebugPreviewWindow::Close() noexcept { impl_->Close(); }

}  // namespace lol_assistant::output
