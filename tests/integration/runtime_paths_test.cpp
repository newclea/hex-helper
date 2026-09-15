#include <Windows.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

struct ProcessResult final {
  DWORD exit_code{0U};
  std::string output{};
};

void Require(const bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

[[nodiscard]] std::string ReadBytes(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  Require(static_cast<bool>(input), "unable to open " + path.string());
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

void CopyFile(const std::filesystem::path& source,
              const std::filesystem::path& destination) {
  std::filesystem::create_directories(destination.parent_path());
  std::filesystem::copy_file(source, destination,
                             std::filesystem::copy_options::overwrite_existing);
}

void WriteReplayPng(const std::filesystem::path& path) {
  constexpr std::array<unsigned char, 68U> kOnePixelPng{
      0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D,
      0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
      0x08, 0x04, 0x00, 0x00, 0x00, 0xB5, 0x1C, 0x0C, 0x02, 0x00, 0x00, 0x00,
      0x0B, 0x49, 0x44, 0x41, 0x54, 0x78, 0xDA, 0x63, 0xFC, 0xFF, 0x1F, 0x00,
      0x02, 0xEB, 0x01, 0xF5, 0x69, 0x43, 0x6F, 0x97, 0x00, 0x00, 0x00, 0x00,
      0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82};
  std::filesystem::create_directories(path.parent_path());
  std::ofstream output(path, std::ios::binary | std::ios::trunc);
  Require(static_cast<bool>(output), "unable to create replay PNG");
  output.write(reinterpret_cast<const char*>(kOnePixelPng.data()),
               static_cast<std::streamsize>(kOnePixelPng.size()));
  Require(static_cast<bool>(output), "unable to write replay PNG");
}

[[nodiscard]] std::wstring QuoteArgument(const std::wstring_view value) {
  std::wstring quoted{L'"'};
  std::size_t backslashes = 0U;
  for (const wchar_t character : value) {
    if (character == L'\\') {
      ++backslashes;
      continue;
    }
    if (character == L'"') {
      quoted.append(backslashes * 2U + 1U, L'\\');
      quoted.push_back(character);
      backslashes = 0U;
      continue;
    }
    quoted.append(backslashes, L'\\');
    backslashes = 0U;
    quoted.push_back(character);
  }
  quoted.append(backslashes * 2U, L'\\');
  quoted.push_back(L'"');
  return quoted;
}

[[nodiscard]] ProcessResult Run(const std::filesystem::path& executable,
                                const std::vector<std::wstring>& arguments,
                                const std::filesystem::path& working_directory,
                                const std::filesystem::path& log_path) {
  std::wstring command_line = QuoteArgument(executable.native());
  for (const auto& argument : arguments) {
    command_line.push_back(L' ');
    command_line.append(QuoteArgument(argument));
  }
  std::vector<wchar_t> mutable_command(command_line.begin(),
                                       command_line.end());
  mutable_command.push_back(L'\0');

  std::filesystem::create_directories(log_path.parent_path());
  SECURITY_ATTRIBUTES security{};
  security.nLength = sizeof(security);
  security.bInheritHandle = TRUE;
  HANDLE log = CreateFileW(log_path.c_str(), GENERIC_WRITE,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, &security,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  Require(log != INVALID_HANDLE_VALUE, "unable to create process log");
  HANDLE null_input =
      CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ, &security,
                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  Require(null_input != INVALID_HANDLE_VALUE,
          "unable to open null process input");

  STARTUPINFOW startup{};
  startup.cb = sizeof(startup);
  startup.dwFlags = STARTF_USESTDHANDLES;
  startup.hStdInput = null_input;
  startup.hStdOutput = log;
  startup.hStdError = log;
  PROCESS_INFORMATION process{};
  const BOOL created = CreateProcessW(
      executable.c_str(), mutable_command.data(), nullptr, nullptr, TRUE,
      CREATE_NO_WINDOW, nullptr, working_directory.c_str(), &startup, &process);
  const DWORD create_error = created == FALSE ? GetLastError() : ERROR_SUCCESS;
  CloseHandle(null_input);
  CloseHandle(log);
  Require(created != FALSE,
          "CreateProcessW failed with error " + std::to_string(create_error));

  const DWORD wait = WaitForSingleObject(process.hProcess, 30000U);
  if (wait == WAIT_TIMEOUT) {
    TerminateProcess(process.hProcess, 124U);
  }
  Require(wait == WAIT_OBJECT_0, "child process timed out or wait failed");
  DWORD exit_code = 0U;
  Require(GetExitCodeProcess(process.hProcess, &exit_code) != FALSE,
          "GetExitCodeProcess failed");
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return {exit_code, ReadBytes(log_path)};
}

[[nodiscard]] std::string JsonPath(const std::filesystem::path& path) {
  std::string value = path.string();
  std::string escaped;
  escaped.reserve(value.size() * 2U);
  for (const char character : value) {
    if (character == '\\') {
      escaped.append("\\\\");
    } else {
      escaped.push_back(character);
    }
  }
  return escaped;
}

[[nodiscard]] std::vector<std::uint8_t> Utf16Le(const std::wstring& text) {
  std::vector<std::uint8_t> bytes;
  bytes.reserve(text.size() * sizeof(wchar_t));
  for (const wchar_t character : text) {
    const auto value = static_cast<std::uint16_t>(character);
    bytes.push_back(static_cast<std::uint8_t>(value & 0xFFU));
    bytes.push_back(static_cast<std::uint8_t>((value >> 8U) & 0xFFU));
  }
  return bytes;
}

void RequireProductPeHasNoSourceManifest(
    const std::filesystem::path& executable,
    const std::filesystem::path& source_manifest) {
  const std::string image = ReadBytes(executable);
  std::wstring native = source_manifest.lexically_normal().wstring();
  std::wstring forward = native;
  std::replace(forward.begin(), forward.end(), L'\\', L'/');
  for (const auto& forbidden : {native, forward}) {
    std::string narrow;
    narrow.reserve(forbidden.size());
    for (const wchar_t character : forbidden) {
      Require(character >= 0 && character <= 0x7F,
              "source manifest path must be ASCII for the PE scan");
      narrow.push_back(static_cast<char>(character));
    }
    Require(image.find(narrow) == std::string::npos,
            "product PE contains the source icon manifest path");
    const auto wide = Utf16Le(forbidden);
    Require(std::search(image.begin(), image.end(), wide.begin(), wide.end()) ==
                image.end(),
            "product PE contains the UTF-16 source icon manifest path");
  }
}

void RequireReplayInitialization(const std::filesystem::path& executable,
                                 const std::filesystem::path& working_directory,
                                 const std::filesystem::path& replay,
                                 const std::filesystem::path& catalog,
                                 const std::filesystem::path& workspace,
                                 const std::filesystem::path& expected_manifest,
                                 const std::filesystem::path& log) {
  const auto result =
      Run(executable,
          {L"--replay", replay.wstring(), L"--knowledge", catalog.wstring(),
           L"--workspace", workspace.wstring(), L"--max-seconds", L"2"},
          working_directory, log);
  Require(result.exit_code == 0U,
          "replay initialization failed: " + result.output);
  Require(
      result.output.find("\"icon_template_count\":245") != std::string::npos,
      "replay did not initialize all icon templates");
  Require(
      result.output.find("\"icon_manifest\":\"" + JsonPath(expected_manifest) +
                         "\"") != std::string::npos,
      "replay selected an unexpected icon manifest: " + result.output);
}

}  // namespace

int wmain(const int argc, wchar_t* argv[]) {
  try {
    Require(argc == 4,
            "usage: runtime_paths_test <exe> <catalog> <icon-manifest>");
    const auto source_executable =
        std::filesystem::absolute(argv[1]).lexically_normal();
    const auto source_catalog =
        std::filesystem::absolute(argv[2]).lexically_normal();
    const auto source_manifest =
        std::filesystem::absolute(argv[3]).lexically_normal();
    RequireProductPeHasNoSourceManifest(source_executable, source_manifest);

    const auto test_root =
        (std::filesystem::current_path() / L"runtime_paths_test")
            .lexically_normal();
    std::filesystem::remove_all(test_root);
    std::filesystem::create_directories(test_root);

    const auto executable_case = test_root / L"exe_relative";
    const auto executable =
        executable_case / L"bin" / L"lol_augment_assistant.exe";
    const auto catalog =
        executable_case / L"data" / L"knowledge" / L"augments.zh-CN.json";
    const auto manifest = executable_case / L"data" / L"knowledge" /
                          L"augment_icons" / L"manifest.json";
    const auto foreign_cwd = executable_case / L"foreign_cwd";
    CopyFile(source_executable, executable);
    CopyFile(source_catalog, catalog);
    CopyFile(source_manifest, manifest);
    std::filesystem::create_directories(foreign_cwd);
    const auto replay = executable_case / L"input" / L"one-pixel.png";
    WriteReplayPng(replay);

    const auto help = Run(executable, {L"--help"}, foreign_cwd,
                          executable_case / L"help.log");
    Require(
        help.exit_code == 0U &&
            help.output.find("augment recognition and live state pipeline") !=
                std::string::npos &&
            help.output.find("--lcu-context <auto|off>") != std::string::npos &&
            help.output.find("Phase1") == std::string::npos,
        "cross-cwd --help is not the current live-state help output");
    RequireReplayInitialization(
        executable, foreign_cwd, replay, catalog, executable_case / L"runtime",
        manifest.lexically_normal(), executable_case / L"replay.log");

    const auto cwd_case = test_root / L"cwd_fallback";
    const auto cwd_executable =
        cwd_case / L"bin" / L"lol_augment_assistant.exe";
    const auto cwd_root = cwd_case / L"working";
    const auto cwd_catalog =
        cwd_root / L"data" / L"knowledge" / L"augments.zh-CN.json";
    const auto cwd_manifest =
        cwd_root / L"data" / L"knowledge" / L"augment_icons" / L"manifest.json";
    CopyFile(source_executable, cwd_executable);
    CopyFile(source_catalog, cwd_catalog);
    CopyFile(source_manifest, cwd_manifest);
    RequireReplayInitialization(
        cwd_executable, cwd_root, replay, cwd_catalog, cwd_case / L"runtime",
        cwd_manifest.lexically_normal(), cwd_case / L"replay.log");

    std::cout << "runtime_paths_test passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "runtime_paths_test failed: " << error.what() << '\n';
    return 1;
  }
}
