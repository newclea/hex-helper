#include "lol_assistant/capture/bounded_queue.h"
#include "lol_assistant/capture/capture_state_machine.h"
#include "lol_assistant/capture/window_info.h"
#include "lol_assistant/capture/windows_graphics_capture_source.h"

#include <dxgi.h>

#include <chrono>
#include <cstdint>
#include <iostream>
#include <optional>
#include <string>
#include <thread>

namespace {

int g_failures = 0;
int g_passed = 0;
int g_skipped = 0;
int g_failed_cases = 0;
int g_passed_cases = 0;

void Check(const bool condition, const char* const expression) {
  if (condition) {
    ++g_passed;
    return;
  }
  ++g_failures;
  std::cerr << "[FAIL] " << expression << '\n';
}

#define CHECK(expression) Check((expression), #expression)

void RunTest(const char* const name, void (*test)()) {
  const int failures_before = g_failures;
  const int skipped_before = g_skipped;
  test();
  if (g_skipped != skipped_before) {
    return;
  }
  if (g_failures == failures_before) {
    ++g_passed_cases;
    std::cout << "[PASS] " << name << '\n';
  } else {
    ++g_failed_cases;
  }
}

constexpr wchar_t kTestWindowClass[] =
    L"LolAssistantCaptureWorkerSmokeWindow";
constexpr wchar_t kTestWindowTitle[] =
    L"LOL Assistant Capture Worker Smoke";

LRESULT CALLBACK TestWindowProcedure(const HWND window, const UINT message,
                                     const WPARAM wparam,
                                     const LPARAM lparam) {
  static_cast<void>(wparam);
  static_cast<void>(lparam);
  if (message == WM_PAINT) {
    PAINTSTRUCT paint{};
    const HDC device_context = BeginPaint(window, &paint);
    const DWORD tick = GetTickCount();
    static_cast<void>(SetDCBrushColor(
        device_context,
        RGB(static_cast<BYTE>(tick & 0xffU),
            static_cast<BYTE>((tick >> 4U) & 0xffU),
            static_cast<BYTE>((tick >> 8U) & 0xffU))));
    const auto brush = static_cast<HBRUSH>(GetStockObject(DC_BRUSH));
    static_cast<void>(FillRect(device_context, &paint.rcPaint, brush));
    EndPaint(window, &paint);
    return 0;
  }
  if (message == WM_ERASEBKGND) {
    return 1;
  }
  return DefWindowProcW(window, message, wparam, lparam);
}

class TestWindow final {
 public:
  TestWindow() {
    instance_ = GetModuleHandleW(nullptr);
    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = &TestWindowProcedure;
    window_class.hInstance = instance_;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.lpszClassName = kTestWindowClass;
    atom_ = RegisterClassExW(&window_class);
    if (atom_ == 0U && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
      return;
    }

    window_ = CreateWindowExW(
        0U, kTestWindowClass, kTestWindowTitle, WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 480, 320, nullptr, nullptr, instance_,
        nullptr);
    if (window_ != nullptr) {
      ShowWindow(window_, SW_SHOWNORMAL);
      static_cast<void>(UpdateWindow(window_));
      PumpMessages();
    }
  }

  ~TestWindow() {
    Close();
    if (atom_ != 0U) {
      static_cast<void>(UnregisterClassW(kTestWindowClass, instance_));
    }
  }

  TestWindow(const TestWindow&) = delete;
  TestWindow& operator=(const TestWindow&) = delete;

  [[nodiscard]] HWND Get() const noexcept { return window_; }

  void Repaint() const {
    if (window_ != nullptr) {
      static_cast<void>(InvalidateRect(window_, nullptr, FALSE));
      static_cast<void>(UpdateWindow(window_));
    }
    PumpMessages();
  }

  void Resize(const int width, const int height) const {
    if (window_ != nullptr) {
      static_cast<void>(SetWindowPos(
          window_, nullptr, 0, 0, width, height,
          SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE));
      Repaint();
    }
  }

  void Close() {
    if (window_ != nullptr) {
      static_cast<void>(DestroyWindow(window_));
      window_ = nullptr;
      PumpMessages();
    }
  }

 private:
  static void PumpMessages() {
    MSG message{};
    while (PeekMessageW(&message, nullptr, 0U, 0U, PM_REMOVE) != FALSE) {
      static_cast<void>(TranslateMessage(&message));
      static_cast<void>(DispatchMessageW(&message));
    }
  }

  HINSTANCE instance_{nullptr};
  ATOM atom_{0U};
  HWND window_{nullptr};
};

void TestEnumerationContract() {
  const auto windows = lol_assistant::capture::EnumerateTopLevelWindows();
  for (const auto& window : windows) {
    CHECK(window.IsValid());
    CHECK(!window.title.empty());
    CHECK(window.process_id != 0U);
  }
  CHECK(!lol_assistant::capture::FindTopLevelWindow(
             L"", lol_assistant::capture::WindowTitleMatchMode::Exact)
             .has_value());
}

void TestInvalidWindowFailsCleanly() {
  auto invalid = reinterpret_cast<HWND>(static_cast<std::uintptr_t>(1U));
  lol_assistant::capture::WindowsGraphicsCaptureSource source(invalid);
  CHECK(source.Statistics().queue_capacity <=
        lol_assistant::capture::WindowsGraphicsCaptureSource::
            kMaximumQueueCapacity);
  std::string error;
  CHECK(!source.Start(&error));
  CHECK(!error.empty());
  CHECK(source.State() == lol_assistant::capture::CaptureState::Failed);
  CHECK(source.LastError().find("startup") != std::string::npos);
  CHECK(source.LastError().find("HWND") != std::string::npos);
  CHECK(!source.TryGetNextFrame().has_value());
  const std::string retained_error = source.LastError();
  source.Stop();
  source.Stop();
  CHECK(source.State() == lol_assistant::capture::CaptureState::Stopped);
  CHECK(source.LastError() == retained_error);
}

void TestBoundedQueueOverflowPolicies() {
  using lol_assistant::capture::BoundedQueue;
  using lol_assistant::capture::QueueOverflowPolicy;
  using lol_assistant::capture::QueuePushResult;

  BoundedQueue<int> drop_newest(2U, QueueOverflowPolicy::DropNewest);
  CHECK(drop_newest.Push(1) == QueuePushResult::Added);
  CHECK(drop_newest.Push(2) == QueuePushResult::Added);
  std::optional<int> rejected;
  CHECK(drop_newest.Push(3, &rejected) == QueuePushResult::DroppedNewest);
  CHECK(rejected == 3);
  CHECK(drop_newest.Size() == 2U);
  CHECK(drop_newest.TryPopOldest() == 1);
  CHECK(drop_newest.TryPopOldest() == 2);
  CHECK(!drop_newest.TryPopOldest().has_value());

  BoundedQueue<int> drop_oldest(2U, QueueOverflowPolicy::DropOldest);
  CHECK(drop_oldest.Push(1) == QueuePushResult::Added);
  CHECK(drop_oldest.Push(2) == QueuePushResult::Added);
  std::optional<int> evicted;
  CHECK(drop_oldest.Push(3, &evicted) == QueuePushResult::DroppedOldest);
  CHECK(evicted == 1);
  CHECK(drop_oldest.Size() == 2U);
  CHECK(drop_oldest.TryPopOldest() == 2);
  CHECK(drop_oldest.TryPopOldest() == 3);
  CHECK(!drop_oldest.TryPopOldest().has_value());

  CHECK(drop_oldest.Push(4) == QueuePushResult::Added);
  CHECK(drop_oldest.Push(5) == QueuePushResult::Added);
  CHECK(drop_oldest.TryPopNewestAndClear() == 5);
  CHECK(drop_oldest.Size() == 0U);
}

void TestResizeFailureStateTransition() {
  using lol_assistant::capture::BoundedQueue;
  using lol_assistant::capture::CaptureState;
  using lol_assistant::capture::QueueOverflowPolicy;
  using lol_assistant::capture::detail::CaptureFailure;
  using lol_assistant::capture::detail::CaptureFailureStage;
  using lol_assistant::capture::detail::CaptureStateMachine;
  using lol_assistant::capture::detail::TransitionCaptureToFailed;

  CaptureStateMachine lifecycle;
  lifecycle.Reset();
  lifecycle.BeginStarting();
  lifecycle.Activate(true);
  CHECK(lifecycle.State() == CaptureState::Running);
  CHECK(lifecycle.IsAcceptingFrames());
  CHECK(!lifecycle.IsStopRequested());

  BoundedQueue<int> queued_frames(3U, QueueOverflowPolicy::DropOldest);
  CHECK(queued_frames.Push(1) ==
        lol_assistant::capture::QueuePushResult::Added);
  CHECK(queued_frames.Push(2) ==
        lol_assistant::capture::QueuePushResult::Added);
  bool caller_woken = false;
  std::size_t discarded_frames = 0U;

  CHECK(lifecycle.BeginResize());
  CHECK(lifecycle.State() == CaptureState::Paused);
  CHECK(!lifecycle.IsAcceptingFrames());
  const bool transitioned = TransitionCaptureToFailed(
      lifecycle,
      CaptureFailure{CaptureFailureStage::ResizeRecreate,
                     static_cast<std::int32_t>(E_FAIL),
                     "injected Recreate failure"},
      [&] { discarded_frames = queued_frames.Clear(); },
      [&] { caller_woken = true; });

  CHECK(transitioned);
  CHECK(lifecycle.State() == CaptureState::Failed);
  CHECK(!lifecycle.IsAcceptingFrames());
  CHECK(lifecycle.IsStopRequested());
  CHECK(caller_woken);
  CHECK(discarded_frames == 2U);
  CHECK(queued_frames.Size() == 0U);
  CHECK(lifecycle.LastError().find("resize Recreate") != std::string::npos);
  CHECK(lifecycle.LastError().find("80004005") != std::string::npos);
  CHECK(lifecycle.LastError().find("injected Recreate failure") !=
        std::string::npos);

  lifecycle.CompleteResize();
  CHECK(lifecycle.State() == CaptureState::Failed);
  lifecycle.BeginStopping();
  CHECK(lifecycle.State() == CaptureState::Failed);
  const std::string retained_error = lifecycle.LastError();
  lifecycle.MarkStopped();
  lifecycle.MarkStopped();
  CHECK(lifecycle.State() == CaptureState::Stopped);
  CHECK(lifecycle.LastError() == retained_error);
}

void TestDeviceLostFailureStateTransition() {
  using lol_assistant::capture::BoundedQueue;
  using lol_assistant::capture::CaptureState;
  using lol_assistant::capture::QueueOverflowPolicy;
  using lol_assistant::capture::detail::CaptureFailure;
  using lol_assistant::capture::detail::CaptureFailureStage;
  using lol_assistant::capture::detail::CaptureStateMachine;
  using lol_assistant::capture::detail::IsUnrecoverableGraphicsError;
  using lol_assistant::capture::detail::TransitionCaptureToFailed;

  const auto removed =
      static_cast<std::int32_t>(DXGI_ERROR_DEVICE_REMOVED);
  const auto reset = static_cast<std::int32_t>(DXGI_ERROR_DEVICE_RESET);
  const auto hung = static_cast<std::int32_t>(DXGI_ERROR_DEVICE_HUNG);
  CHECK(IsUnrecoverableGraphicsError(removed));
  CHECK(IsUnrecoverableGraphicsError(reset));
  CHECK(IsUnrecoverableGraphicsError(hung));
  CHECK(!IsUnrecoverableGraphicsError(static_cast<std::int32_t>(E_INVALIDARG)));

  CaptureStateMachine lifecycle;
  lifecycle.Reset();
  lifecycle.BeginStarting();
  lifecycle.Activate(true);
  BoundedQueue<int> queued_frames(3U, QueueOverflowPolicy::DropOldest);
  CHECK(queued_frames.Push(10) ==
        lol_assistant::capture::QueuePushResult::Added);
  CHECK(queued_frames.Push(11) ==
        lol_assistant::capture::QueuePushResult::Added);
  bool latest_frame_available = true;
  int wake_count = 0;

  CHECK(TransitionCaptureToFailed(
      lifecycle,
      CaptureFailure{CaptureFailureStage::FrameConversion, removed,
                     "injected device removed"},
      [&] {
        static_cast<void>(queued_frames.Clear());
        latest_frame_available = false;
      },
      [&] { ++wake_count; }));
  CHECK(lifecycle.State() == CaptureState::Failed);
  CHECK(!lifecycle.IsAcceptingFrames());
  CHECK(lifecycle.IsStopRequested());
  CHECK(queued_frames.Size() == 0U);
  CHECK(!latest_frame_available);
  CHECK(wake_count == 1);
  CHECK(lifecycle.LastError().find("worker frame conversion") !=
        std::string::npos);
  CHECK(lifecycle.LastError().find("887A0005") != std::string::npos);
  CHECK(lifecycle.LastError().find("injected device removed") !=
        std::string::npos);

  const std::string first_error = lifecycle.LastError();
  CHECK(!TransitionCaptureToFailed(
      lifecycle,
      CaptureFailure{CaptureFailureStage::FrameConversion, reset,
                     "later device reset"},
      [] {}, [&] { ++wake_count; }));
  CHECK(wake_count == 1);
  CHECK(lifecycle.LastError() == first_error);
}

lol_assistant::common::CapturedFrame CapturedForEpoch(
    const std::uint64_t epoch, const std::uint64_t frame_id,
    const std::uint8_t value) {
  lol_assistant::common::CapturedFrame captured;
  captured.frame.source = {
      lol_assistant::common::FrameSourceKind::WindowsGraphicsCapture,
      "wgc-epoch-mailbox-test"};
  captured.frame.frame_id = frame_id;
  captured.frame.width = 1U;
  captured.frame.height = 1U;
  captured.frame.stride = 4U;
  captured.frame.buffer.assign(4U, value);
  captured.identity = {captured.frame.source, epoch, frame_id};
  return captured;
}

void TestWgcEpochMailboxQuarantinesOldCallbackAndQueue() {
  using lol_assistant::capture::detail::WgcCapturedFrameMailbox;
  using lol_assistant::capture::detail::WgcFramePublishResult;

  WgcCapturedFrameMailbox mailbox;
  const std::uint64_t old_epoch = mailbox.BeginCaptureEpoch();
  CHECK(old_epoch != 0U);
  CHECK(mailbox.Publish(old_epoch, CapturedForEpoch(old_epoch, 1U, 1U)) ==
        WgcFramePublishResult::Published);

  const std::uint64_t current_epoch = mailbox.BeginCaptureEpoch();
  CHECK(current_epoch > old_epoch);
  CHECK(!mailbox.Take().has_value());
  CHECK(mailbox.Publish(old_epoch, CapturedForEpoch(old_epoch, 2U, 2U)) ==
        WgcFramePublishResult::RejectedEpoch);
  CHECK(!mailbox.Take().has_value());
  CHECK(mailbox.Publish(current_epoch,
                        CapturedForEpoch(current_epoch, 1U, 3U)) ==
        WgcFramePublishResult::Published);
  const auto current = mailbox.Take();
  CHECK(current.has_value());
  CHECK(current.has_value() && current->IsValid());
  CHECK(current.has_value() &&
        current->identity.capture_epoch == current_epoch);
  CHECK(mailbox.CurrentCaptureEpoch() == current_epoch);
}

void TestOwnWindowCaptureSmoke() {
  using namespace std::chrono_literals;
  using lol_assistant::capture::CaptureState;
  using lol_assistant::capture::FindTopLevelWindow;
  using lol_assistant::capture::WindowTitleMatchMode;
  using lol_assistant::capture::WindowsGraphicsCaptureSource;

  TestWindow test_window;
  CHECK(test_window.Get() != nullptr);
  if (test_window.Get() == nullptr) {
    return;
  }

  const auto exact =
      FindTopLevelWindow(kTestWindowTitle, WindowTitleMatchMode::Exact);
  CHECK(exact.has_value());
  CHECK(exact.has_value() && exact->handle == test_window.Get());
  const auto substring = FindTopLevelWindow(
      L"cApTuRe wOrKeR sMoKe",
      WindowTitleMatchMode::CaseInsensitiveSubstring);
  CHECK(substring.has_value());
  CHECK(substring.has_value() && substring->handle == test_window.Get());

  std::string support_reason;
  if (!WindowsGraphicsCaptureSource::IsSupported(&support_reason)) {
    ++g_skipped;
    std::cout << "[SKIP] own-window WGC smoke: " << support_reason << '\n';
    return;
  }

  WindowsGraphicsCaptureSource source(test_window.Get(), 3U);
  std::string start_error;
  CHECK(source.Start(&start_error));
  if (!start_error.empty()) {
    std::cerr << "[INFO] WGC start error: " << start_error << '\n';
  }
  CHECK(source.State() == CaptureState::Running ||
        source.State() == CaptureState::Paused);
  const std::uint64_t initial_epoch = source.CurrentCaptureEpoch();
  CHECK(initial_epoch != 0U);
  CHECK(source.Start(&start_error));
  CHECK(source.CurrentCaptureEpoch() == initial_epoch);

  const auto started = std::chrono::steady_clock::now();
  const auto consumer_wake = started + 750ms;
  const auto initial_frame_deadline = started + 2s;
  const auto resize_deadline = started + 3500ms;
  while (std::chrono::steady_clock::now() < consumer_wake) {
    test_window.Repaint();
    std::this_thread::sleep_for(16ms);
  }

  auto received_frame = source.TryGetNextFrame();
  while (!received_frame.has_value() &&
         std::chrono::steady_clock::now() < initial_frame_deadline) {
    test_window.Repaint();
    std::this_thread::sleep_for(16ms);
    received_frame = source.TryGetNextFrame();
  }

  const std::uint32_t initial_width =
      received_frame.has_value() ? received_frame->width : 0U;
  const std::uint32_t initial_height =
      received_frame.has_value() ? received_frame->height : 0U;
  test_window.Resize(640, 360);
  std::optional<lol_assistant::common::Frame> resized_frame;
  while (std::chrono::steady_clock::now() < resize_deadline) {
    test_window.Repaint();
    std::this_thread::sleep_for(16ms);
    auto candidate = source.TryGetNextFrame();
    if (candidate.has_value() &&
        (candidate->width != initial_width ||
         candidate->height != initial_height)) {
      resized_frame = std::move(candidate);
      break;
    }
  }

  const auto statistics = source.Statistics();
  std::cout << "[SMOKE] received=" << statistics.received
            << " converted=" << statistics.converted
            << " dropped=" << statistics.dropped
            << " queued=" << statistics.queued_frames << '/'
            << statistics.queue_capacity << " initial=" << initial_width << 'x'
            << initial_height << " resized="
            << (resized_frame.has_value() ? resized_frame->width : 0U) << 'x'
            << (resized_frame.has_value() ? resized_frame->height : 0U) << '\n';
  CHECK(received_frame.has_value());
  CHECK(received_frame.has_value() && received_frame->IsValid());
  CHECK(resized_frame.has_value());
  CHECK(resized_frame.has_value() && resized_frame->IsValid());
  CHECK(statistics.received >= 1U);
  CHECK(statistics.converted >= 1U);
  CHECK(statistics.dropped >= 1U);
  CHECK(statistics.queued_frames <= 3U);
  CHECK(statistics.latest_frame_age.has_value());
  CHECK(statistics.conversion_p50_ms.has_value());
  CHECK(statistics.conversion_p95_ms.has_value());

  source.Stop();
  source.Stop();
  CHECK(source.State() == CaptureState::Stopped);
  CHECK(source.LastError().empty());

  CHECK(source.Start(&start_error));
  CHECK(start_error.empty());
  const std::uint64_t restarted_epoch = source.CurrentCaptureEpoch();
  CHECK(restarted_epoch > initial_epoch);
  std::optional<lol_assistant::common::CapturedFrame> restarted_frame;
  const auto restarted_frame_deadline =
      std::chrono::steady_clock::now() + 2s;
  while (!restarted_frame.has_value() &&
         std::chrono::steady_clock::now() < restarted_frame_deadline) {
    test_window.Repaint();
    std::this_thread::sleep_for(16ms);
    restarted_frame = source.TryGetNextCapturedFrame();
  }
  CHECK(restarted_frame.has_value());
  CHECK(restarted_frame.has_value() && restarted_frame->IsValid());
  CHECK(restarted_frame.has_value() &&
        restarted_frame->identity.capture_epoch == restarted_epoch);

  test_window.Close();
  const auto close_deadline = std::chrono::steady_clock::now() + 2s;
  while (source.State() != CaptureState::Closed &&
         std::chrono::steady_clock::now() < close_deadline) {
    std::this_thread::sleep_for(10ms);
  }
  std::cout << "[SMOKE] terminal_state="
            << static_cast<int>(source.State()) << '\n';
  CHECK(source.State() == CaptureState::Closed);
  source.Stop();
  source.Stop();
  CHECK(source.State() == CaptureState::Stopped);
}

}  // namespace

int main() {
  RunTest("window enumeration contract", &TestEnumerationContract);
  RunTest("invalid HWND and idempotent Stop",
          &TestInvalidWindowFailsCleanly);
  RunTest("bounded queue overflow policies", &TestBoundedQueueOverflowPolicies);
  RunTest("resize Recreate failure transitions to Failed",
          &TestResizeFailureStateTransition);
  RunTest("device-lost conversion failure transitions to Failed",
          &TestDeviceLostFailureStateTransition);
  RunTest("WGC epoch mailbox quarantines old callback and queue",
          &TestWgcEpochMailboxQuarantinesOldCallbackAndQueue);
  RunTest("own visible window WGC smoke and idempotent Start/Stop",
          &TestOwnWindowCaptureSmoke);
  std::cout << "capture_minimal_test: passed_cases=" << g_passed_cases
            << " failed_cases=" << g_failed_cases
            << " skipped_cases=" << g_skipped
            << " passed_assertions=" << g_passed
            << " failed_assertions=" << g_failures << '\n';
  return g_failures == 0 ? 0 : 1;
}
