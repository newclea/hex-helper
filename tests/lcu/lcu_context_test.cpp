// clang-format off: Winsock must precede Windows headers.
#include <winsock2.h>
#include <windows.h>
// clang-format on

#include "lol_assistant/lcu/lcu_context.h"

#include <winrt/base.h>

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

using lol_assistant::lcu::EnvironmentLcuConnectionProvider;
using lol_assistant::lcu::ILcuConnectionProvider;
using lol_assistant::lcu::ILcuContextTransport;
using lol_assistant::lcu::LcuConnection;
using lol_assistant::lcu::LcuConnectionResult;
using lol_assistant::lcu::LcuContext;
using lol_assistant::lcu::LcuContextEmissionGate;
using lol_assistant::lcu::LcuContextEvent;
using lol_assistant::lcu::LcuContextPoller;
using lol_assistant::lcu::LcuContextPollerConfig;
using lol_assistant::lcu::LcuContextReader;
using lol_assistant::lcu::LcuContextSnapshot;
using lol_assistant::lcu::LcuContextStatus;
using lol_assistant::lcu::LcuEndpoint;
using lol_assistant::lcu::LcuHttpResult;
using lol_assistant::lcu::ParseCurrentChampionId;
using lol_assistant::lcu::ParseGameflowPhase;
using lol_assistant::lcu::ParseLcuLaunchArguments;
using lol_assistant::lcu::SerializeLcuContextEventJson;

constexpr char kTokenSentinel[] = "LCU_TEST_TOKEN_DO_NOT_PRINT";

void Require(const bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

class FixedProvider final : public ILcuConnectionProvider {
 public:
  LcuConnectionResult result{LcuConnection{61234U, kTokenSentinel}, "ok"};
  std::size_t calls{0U};
  std::size_t invalidations{0U};

  [[nodiscard]] LcuConnectionResult Resolve() override {
    ++calls;
    return result;
  }
  void Invalidate() noexcept override { ++invalidations; }
};

class FixedTransport final : public ILcuContextTransport {
 public:
  LcuHttpResult phase;
  LcuHttpResult champion;
  std::vector<LcuEndpoint> endpoints;

  [[nodiscard]] LcuHttpResult Get(const LcuConnection& connection,
                                  const LcuEndpoint endpoint) override {
    Require(connection.token == kTokenSentinel,
            "reader must pass credentials only to its transport");
    endpoints.push_back(endpoint);
    return endpoint == LcuEndpoint::GameflowPhase ? phase : champion;
  }
};

[[nodiscard]] LcuHttpResult Ok(std::string body) {
  return LcuHttpResult{true, 200U, std::move(body), {}};
}

[[nodiscard]] std::optional<std::wstring> EnvironmentValue(
    const wchar_t* const name) {
  const DWORD required = GetEnvironmentVariableW(name, nullptr, 0U);
  if (required == 0U) {
    return std::nullopt;
  }
  std::wstring value(static_cast<std::size_t>(required), L'\0');
  const DWORD written = GetEnvironmentVariableW(name, value.data(), required);
  if (written == 0U || written >= required) {
    return std::nullopt;
  }
  value.resize(static_cast<std::size_t>(written));
  return value;
}

class EnvironmentGuard final {
 public:
  explicit EnvironmentGuard(const wchar_t* const name)
      : name_(name), previous_(EnvironmentValue(name)) {}
  ~EnvironmentGuard() {
    SetEnvironmentVariableW(
        name_.c_str(), previous_.has_value() ? previous_->c_str() : nullptr);
  }

 private:
  std::wstring name_;
  std::optional<std::wstring> previous_;
};

class WinsockSession final {
 public:
  WinsockSession() {
    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) {
      throw std::runtime_error("WSAStartup failed");
    }
  }
  ~WinsockSession() { WSACleanup(); }
};

class ListeningSocket final {
 public:
  ListeningSocket() {
    socket_ = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (socket_ == INVALID_SOCKET) {
      throw std::runtime_error("unable to create discovery test socket");
    }
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = 0U;
    if (bind(socket_, reinterpret_cast<const sockaddr*>(&address),
             sizeof(address)) == SOCKET_ERROR ||
        listen(socket_, 1) == SOCKET_ERROR) {
      closesocket(socket_);
      socket_ = INVALID_SOCKET;
      throw std::runtime_error("unable to bind discovery test socket");
    }
    int address_bytes = sizeof(address);
    if (getsockname(socket_, reinterpret_cast<sockaddr*>(&address),
                    &address_bytes) == SOCKET_ERROR) {
      throw std::runtime_error("unable to inspect discovery test socket");
    }
    port_ = ntohs(address.sin_port);
  }
  ~ListeningSocket() {
    if (socket_ != INVALID_SOCKET) {
      closesocket(socket_);
    }
  }
  [[nodiscard]] std::uint16_t port() const noexcept { return port_; }

 private:
  SOCKET socket_{INVALID_SOCKET};
  std::uint16_t port_{0U};
};

void TestLaunchArgumentParser() {
  std::string reason;
  const std::string line =
      std::string{"prefix --app-pid=4242 --app-port \"61234\" "} +
      "--remoting-auth-token='" + kTokenSentinel +
      "'\nrepeat --app-pid=4242 --app-port=61234 "
      "--remoting-auth-token=" +
      kTokenSentinel;
  const auto parsed = ParseLcuLaunchArguments(line, reason);
  Require(parsed.has_value() && parsed->app_pid == 4242U &&
              parsed->port == 61234U && parsed->token == kTokenSentinel &&
              reason == "ok",
          "standard repeated LeagueClientUx arguments must parse exactly");

  Require(!ParseLcuLaunchArguments(line + " --app-port=61235", reason)
                  .has_value() &&
              reason == "launch_arguments_conflict",
          "conflicting repeated launch arguments must fail closed");
  Require(!ParseLcuLaunchArguments(
               "--app-pid=1 --app-port=70000 --remoting-auth-token=x", reason)
                  .has_value() &&
              reason == "launch_arguments_invalid",
          "out-of-range LCU ports must be rejected");
  Require(!ParseLcuLaunchArguments("--app-pid=1 --app-port=61234", reason)
                  .has_value() &&
              reason == "launch_arguments_missing",
          "missing credentials must fail closed");
}

void TestLogDiscoveryWithPidAndLoopbackOwnership() {
  EnvironmentGuard port_guard{L"LOL_ASSISTANT_LCU_PORT"};
  EnvironmentGuard token_guard{L"LOL_ASSISTANT_LCU_TOKEN"};
  EnvironmentGuard root_guard{L"LOL_ASSISTANT_LEAGUE_ROOT"};
  SetEnvironmentVariableW(L"LOL_ASSISTANT_LCU_PORT", nullptr);
  SetEnvironmentVariableW(L"LOL_ASSISTANT_LCU_TOKEN", nullptr);

  WinsockSession winsock;
  ListeningSocket listener;
  const auto root =
      std::filesystem::current_path() /
      (L"lcu_discovery_" + std::to_wstring(GetCurrentProcessId()));
  const auto log_directory = root / L"LeagueClient";
  std::filesystem::create_directories(log_directory);
  const auto log_path = log_directory / L"test_LeagueClientUx.log";
  std::ofstream output(log_path, std::ios::binary | std::ios::trunc);
  Require(static_cast<bool>(output), "discovery fixture log must be writable");
  output << "--app-pid=" << GetCurrentProcessId()
         << " --app-port=" << listener.port()
         << " --remoting-auth-token=" << kTokenSentinel << '\n';
  output.close();
  SetEnvironmentVariableW(L"LOL_ASSISTANT_LEAGUE_ROOT", root.wstring().c_str());

  EnvironmentLcuConnectionProvider provider;
  const auto resolved = provider.Resolve();
  Require(resolved.connection.has_value() && resolved.reason == "ok" &&
              resolved.connection->port == listener.port() &&
              resolved.connection->token == kTokenSentinel,
          "log discovery must require an active matching loopback PID/port");

  std::ofstream invalid_log(log_path, std::ios::binary | std::ios::trunc);
  invalid_log << "invalidated fixture";
  invalid_log.close();
  const auto cached = provider.Resolve();
  Require(cached.connection.has_value() &&
              cached.connection->port == listener.port(),
          "validated LCU credentials must be cached without rereading logs");
  provider.Invalidate();
  Require(!provider.Resolve().connection.has_value(),
          "credential invalidation must force fresh log discovery");
}

void TestStrictJsonParsers() {
  std::string reason;
  Require(ParseGameflowPhase(R"json("ChampSelect")json", reason) ==
                  std::optional<std::string>{"ChampSelect"} &&
              reason.empty(),
          "gameflow phase must be a bounded JSON string");
  Require(!ParseGameflowPhase(R"json({"phase":"ChampSelect"})json", reason)
                  .has_value() &&
              reason == "gameflow_invalid_response",
          "gameflow objects must not be coerced");
  Require(!ParseGameflowPhase(R"json("")json", reason).has_value(),
          "blank gameflow phases must be rejected");

  Require(ParseCurrentChampionId("103", reason) ==
              std::optional<std::uint32_t>{103U},
          "positive integral champion IDs must parse");
  Require(!ParseCurrentChampionId("0", reason).has_value() &&
              reason == "champion_unavailable",
          "zero means no champion is currently selected");
  for (const std::string value : {"true", "-1", "1.5", "\"103\"", "{}"}) {
    Require(!ParseCurrentChampionId(value, reason).has_value() &&
                reason == "champion_invalid_response",
            "wrong champion ID types and ranges must fail closed");
  }
}

void TestReaderRoutingAndFailOpenState() {
  auto provider = std::make_unique<FixedProvider>();
  auto transport = std::make_unique<FixedTransport>();
  auto* const transport_view = transport.get();
  transport->phase = Ok(R"json("Lobby")json");
  transport->champion = Ok("103");
  LcuContextReader reader{std::move(provider), std::move(transport)};
  auto snapshot = reader.Read();
  Require(snapshot.status == LcuContextStatus::Ready &&
              snapshot.context.has_value() &&
              snapshot.context->gameflow_phase == "Lobby" &&
              !snapshot.context->champion_id.has_value() &&
              transport_view->endpoints ==
                  std::vector<LcuEndpoint>{LcuEndpoint::GameflowPhase},
          "non-ChampSelect phases must not call the champion endpoint");

  transport_view->phase = Ok(R"json("ChampSelect")json");
  snapshot = reader.Read();
  Require(
      snapshot.status == LcuContextStatus::Ready &&
          snapshot.context->champion_id == std::optional<std::uint32_t>{103U} &&
          transport_view->endpoints.back() == LcuEndpoint::CurrentChampion,
      "ChampSelect must add a current champion ID");
  transport_view->phase = Ok(R"json("InProgress")json");
  snapshot = reader.Read();
  Require(snapshot.status == LcuContextStatus::Ready &&
              !snapshot.context->champion_id.has_value(),
          "leaving ChampSelect must immediately clear the old champion ID");

  auto missing_provider = std::make_unique<FixedProvider>();
  missing_provider->result =
      LcuConnectionResult{std::nullopt, "auth_unavailable"};
  auto unused_transport = std::make_unique<FixedTransport>();
  auto* const unused_view = unused_transport.get();
  LcuContextReader missing_reader{std::move(missing_provider),
                                  std::move(unused_transport)};
  const auto missing = missing_reader.Read();
  Require(missing.status == LcuContextStatus::Unavailable &&
              missing.reason == "auth_unavailable" &&
              unused_view->endpoints.empty(),
          "missing LCU credentials must be an optional unavailable state");

  auto rejected_provider = std::make_unique<FixedProvider>();
  auto* const rejected_provider_view = rejected_provider.get();
  auto rejected_transport = std::make_unique<FixedTransport>();
  rejected_transport->phase = LcuHttpResult{false, 401U, {}, "auth_rejected"};
  LcuContextReader rejected_reader{std::move(rejected_provider),
                                   std::move(rejected_transport)};
  const auto rejected = rejected_reader.Read();
  Require(rejected.status == LcuContextStatus::Unavailable &&
              rejected.reason == "auth_rejected" &&
              rejected_provider_view->invalidations == 1U,
          "401 must safely invalidate discovered LCU credentials");

  auto partial_provider = std::make_unique<FixedProvider>();
  auto partial_transport = std::make_unique<FixedTransport>();
  partial_transport->phase = Ok(R"json("ChampSelect")json");
  partial_transport->champion = Ok("0");
  LcuContextReader partial_reader{std::move(partial_provider),
                                  std::move(partial_transport)};
  const auto partial = partial_reader.Read();
  Require(partial.status == LcuContextStatus::Partial &&
              partial.reason == "champion_unavailable" &&
              partial.context.has_value() &&
              !partial.context->champion_id.has_value(),
          "an unselected champion must preserve phase but not invent an ID");
}

void TestEmissionGateJsonAndPoller() {
  LcuContextSnapshot snapshot;
  snapshot.status = LcuContextStatus::Ready;
  snapshot.reason = "ok";
  snapshot.context = LcuContext{"ChampSelect", 103U};
  snapshot.observed_at = std::chrono::system_clock::time_point{
      std::chrono::seconds{1'700'000'000}};

  LcuContextEmissionGate gate{std::chrono::seconds{30}};
  const auto start = std::chrono::steady_clock::time_point{};
  Require(gate.ShouldEmit(snapshot, start), "first LCU state must emit");
  Require(!gate.ShouldEmit(snapshot, start + std::chrono::seconds{1}),
          "unchanged LCU state must be deduplicated");
  Require(gate.ShouldEmit(snapshot, start + std::chrono::seconds{30}),
          "unchanged LCU state must retain a bounded heartbeat");

  const std::string json =
      SerializeLcuContextEventJson(LcuContextEvent{7U, snapshot}, "session-1");
  Require(
      json.find("\"type\":\"lcu_context_state\"") != std::string::npos &&
          json.find("\"status\":\"READY\"") != std::string::npos &&
          json.find("\"gameflowPhase\":\"ChampSelect\"") != std::string::npos &&
          json.find("\"championId\":103") != std::string::npos &&
          json.find(kTokenSentinel) == std::string::npos &&
          json.find("authorization") == std::string::npos &&
          json.find('\n') == std::string::npos,
      "LCU JSON must be one credential-free context line");

  auto provider = std::make_unique<FixedProvider>();
  auto transport = std::make_unique<FixedTransport>();
  transport->phase = Ok(R"json("Lobby")json");
  auto reader = std::make_unique<LcuContextReader>(std::move(provider),
                                                   std::move(transport));
  std::mutex mutex;
  std::condition_variable condition;
  std::size_t callbacks = 0U;
  LcuContextPoller poller{std::move(reader),
                          LcuContextPollerConfig{std::chrono::milliseconds{5},
                                                 std::chrono::hours{1}},
                          [&](const LcuContextEvent&) {
                            std::lock_guard lock(mutex);
                            ++callbacks;
                            condition.notify_all();
                          }};
  poller.Start();
  {
    std::unique_lock lock(mutex);
    Require(condition.wait_for(lock, std::chrono::seconds{1},
                               [&] { return callbacks > 0U; }),
            "LCU poller must emit its first state promptly");
  }
  std::this_thread::sleep_for(std::chrono::milliseconds{20});
  poller.Stop();
  Require(!poller.running() && callbacks == 1U,
          "poller stop must join and unchanged state must not flood output");
}

}  // namespace

int main() {
  bool apartment_initialized = false;
  try {
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    apartment_initialized = true;
    TestLaunchArgumentParser();
    TestLogDiscoveryWithPidAndLoopbackOwnership();
    TestStrictJsonParsers();
    TestReaderRoutingAndFailOpenState();
    TestEmissionGateJsonAndPoller();
    winrt::uninit_apartment();
    std::cout << "lcu_context_test passed: discovery=pid_bound; "
                 "routing=allowlisted; output=credential_free\n";
    return 0;
  } catch (const std::exception& error) {
    if (apartment_initialized) {
      winrt::uninit_apartment();
    }
    std::cerr << "lcu_context_test failed: " << error.what() << '\n';
    return 1;
  }
}
