param(
    [string]$VoiceName
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function Write-Event {
    param(
        [string]$EventName,
        [string]$Message = ""
    )
    $payload = [ordered]@{ event = $EventName }
    if ($Message) {
        $payload.message = $Message
    }
    $line = $payload | ConvertTo-Json -Compress
    [Console]::Out.WriteLine($line)
    [Console]::Out.Flush()
}

function Select-SpeechVoice {
    param(
        $Synthesizer,
        [string]$ConfiguredVoice
    )
    $voices = @($Synthesizer.GetInstalledVoices() | Where-Object { $_.Enabled })
    if ($ConfiguredVoice) {
        $configured = $voices | Where-Object { $_.VoiceInfo.Name -eq $ConfiguredVoice } | Select-Object -First 1
        if ($configured) {
            $Synthesizer.SelectVoice($configured.VoiceInfo.Name)
            return
        }
    }
    $chineseFemale = $voices | Where-Object {
        $_.VoiceInfo.Culture.Name -eq "zh-CN" -and
        $_.VoiceInfo.Gender -eq [System.Speech.Synthesis.VoiceGender]::Female
    } | Select-Object -First 1
    if ($chineseFemale) {
        $Synthesizer.SelectVoice($chineseFemale.VoiceInfo.Name)
    }
}

try {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    Select-SpeechVoice -Synthesizer $synth -ConfiguredVoice $VoiceName
    Register-ObjectEvent -InputObject $synth -EventName SpeakStarted -Action {
        [Console]::Out.WriteLine('{"event":"started"}')
        [Console]::Out.Flush()
    } | Out-Null
    Register-ObjectEvent -InputObject $synth -EventName SpeakCompleted -Action {
        if ($EventArgs.Error) {
            $payload = [ordered]@{
                event = "error"
                message = $EventArgs.Error.Message
            }
            [Console]::Out.WriteLine(($payload | ConvertTo-Json -Compress))
        }
        else {
            [Console]::Out.WriteLine('{"event":"finished"}')
        }
        [Console]::Out.Flush()
    } | Out-Null
    Write-Event -EventName "ready"

    while (($line = [Console]::In.ReadLine()) -ne $null) {
        try {
            $message = $line | ConvertFrom-Json
            switch ([string]$message.command) {
                "speak" {
                    $null = $synth.SpeakAsync([string]$message.text)
                }
                "cancel" {
                    $synth.SpeakAsyncCancelAll()
                }
                "close" {
                    $synth.SpeakAsyncCancelAll()
                    $synth.Dispose()
                    exit 0
                }
                default {
                    Write-Event -EventName "error" -Message "Unknown command"
                }
            }
        }
        catch {
            Write-Event -EventName "error" -Message $_.Exception.Message
        }
    }
    $synth.SpeakAsyncCancelAll()
    $synth.Dispose()
}
catch {
    Write-Event -EventName "error" -Message $_.Exception.Message
    exit 1
}
