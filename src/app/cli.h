#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace lol_assistant::app {

enum class Command { Run, Help, Version, ListWindows, ProbeLiveClient };
enum class SourceKind { None, Replay, WindowHandle, WindowTitle };
enum class ReplayKind { Image, Directory, ManifestJsonLines };
enum class CaptureBackend { Auto, Wgc, Desktop };
enum class LiveClientMode { Auto, Off };
enum class LcuContextMode { Auto, Off };
enum class Mode { Kiwi, KiwiJade, Cherry, Auto };
enum class SelectedPosition { Left, Center, Right };
enum class SessionStopReason {
  InputExhausted,
  RecognitionAttemptCompleted,
  Timeout,
};
enum class RecognitionSessionStatus {
  Completed,
  CompletedUnknown,
  CompletedNoStableObservation,
  Timeout,
};

inline constexpr std::size_t kStaticReplayPassCount = 5U;
inline constexpr std::uint32_t kForceRecognitionBurstFrames = 5U;
inline constexpr std::uint32_t kForceRecognitionBurstMilliseconds = 1'500U;
inline constexpr std::wstring_view kLeagueGameWindowTitle =
    L"League of Legends (TM) Client";
inline constexpr std::wstring_view kLeagueLauncherWindowTitle =
    L"League of Legends";

struct RecognitionProgress final {
  bool stable_observation_seen{false};
  bool ocr_executed{false};
  bool accepted_offer{false};
  bool recognition_attempt_completed{false};
};

class PassiveHotkeyEdge final {
public:
  void Prime(bool down) noexcept { down_ = down; }
  [[nodiscard]] bool Observe(bool down) noexcept;

private:
  bool down_{false};
};

class ForceRecognitionBurst final {
public:
  void Trigger() noexcept;
  void ObserveFrame(bool completed) noexcept;
  void Cancel() noexcept { remaining_frames_ = 0U; }

  [[nodiscard]] bool active() const noexcept { return remaining_frames_ > 0U; }
  [[nodiscard]] std::uint32_t remaining_frames() const noexcept {
    return remaining_frames_;
  }

private:
  std::uint32_t remaining_frames_{0U};
};

struct ApplicationOptions {
  Command command{Command::Run};
  SourceKind source_kind{SourceKind::None};
  ReplayKind replay_kind{ReplayKind::Image};
  CaptureBackend capture_backend{CaptureBackend::Auto};
  bool capture_backend_explicit{false};
  LiveClientMode live_client_mode{LiveClientMode::Auto};
  bool live_client_mode_explicit{false};
  LcuContextMode lcu_context_mode{LcuContextMode::Auto};
  bool lcu_context_mode_explicit{false};
  std::filesystem::path replay_path;
  std::uintptr_t window_handle{0};
  std::wstring window_title;
  std::wstring champion;
  Mode mode{Mode::Kiwi};
  std::filesystem::path knowledge_path;
  std::filesystem::path workspace_path;
  bool collect_samples{false};
  std::filesystem::path dataset_root;
  bool sample_hotkey_enabled{true};
  bool force_recognition_hotkey_enabled{true};
  std::uint32_t completed_offers{0U};
  std::optional<double> collect_suspect_confidence;
  bool preview{false};
  double max_seconds{30.0};
  bool once{false};
  std::optional<SelectedPosition> selected;
};

struct ParseResult {
  std::optional<ApplicationOptions> options;
  std::string error;

  [[nodiscard]] bool ok() const noexcept { return options.has_value(); }
};

struct WindowCandidate {
  std::uintptr_t handle{0};
  std::wstring title;
  bool visible{false};
};

struct WindowSelectionResult {
  std::optional<std::uintptr_t> handle;
  std::vector<WindowCandidate> candidates;
  std::string error;

  [[nodiscard]] bool ok() const noexcept { return handle.has_value(); }
};

[[nodiscard]] ParseResult
ParseCommandLine(const std::vector<std::wstring> &arguments,
                 const std::filesystem::path &current_directory =
                     std::filesystem::current_path());

[[nodiscard]] WindowSelectionResult
SelectWindowByTitle(std::wstring_view exact_title,
                    const std::vector<WindowCandidate> &windows);

[[nodiscard]] WindowSelectionResult
SelectWindowByHandle(std::uintptr_t handle,
                     const std::vector<WindowCandidate> &windows);

void RecordRecognitionObservation(RecognitionProgress &progress,
                                  bool stable_observation, bool ocr_executed,
                                  bool accepted_offer,
                                  std::string_view reason) noexcept;

[[nodiscard]] bool
ShouldStopAfterRecognitionAttempt(bool once,
                                  const RecognitionProgress &progress) noexcept;

[[nodiscard]] RecognitionSessionStatus
ResolveRecognitionSessionStatus(SessionStopReason stop_reason,
                                const RecognitionProgress &progress) noexcept;

[[nodiscard]] const char *ToString(Mode mode) noexcept;
[[nodiscard]] const char *ToString(ReplayKind replay_kind) noexcept;
[[nodiscard]] const char *ToString(CaptureBackend backend) noexcept;
[[nodiscard]] const char *ToString(LiveClientMode mode) noexcept;
[[nodiscard]] const char *ToString(LcuContextMode mode) noexcept;
[[nodiscard]] const char *ToString(SelectedPosition selected) noexcept;
[[nodiscard]] const char *ToString(RecognitionSessionStatus status) noexcept;

} // namespace lol_assistant::app
