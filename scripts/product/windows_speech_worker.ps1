param(
    [string]$VoiceName
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function global:Write-SpeechJsonEvent {
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

function Publish-PendingSpeechEvents {
    param(
        [string]$StartedIdentifier,
        [string]$CompletedIdentifier
    )
    foreach ($speechEvent in @(Get-Event)) {
        try {
            if ($speechEvent.SourceIdentifier -eq $StartedIdentifier) {
                Write-SpeechJsonEvent -EventName "started"
            }
            elseif ($speechEvent.SourceIdentifier -eq $CompletedIdentifier) {
                $eventArgs = $speechEvent.SourceEventArgs
                if ($eventArgs.Error) {
                    Write-SpeechJsonEvent -EventName "error" -Message $eventArgs.Error.Message
                }
                else {
                    Write-SpeechJsonEvent -EventName "finished"
                }
            }
        }
        finally {
            Remove-Event -EventIdentifier $speechEvent.EventIdentifier -ErrorAction SilentlyContinue
        }
    }
}

try {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    Select-SpeechVoice -Synthesizer $synth -ConfiguredVoice $VoiceName
    $startedIdentifier = "GameBuddy.Speech.Started"
    $completedIdentifier = "GameBuddy.Speech.Completed"
    Register-ObjectEvent -InputObject $synth -EventName SpeakStarted `
        -SourceIdentifier $startedIdentifier | Out-Null
    Register-ObjectEvent -InputObject $synth -EventName SpeakCompleted `
        -SourceIdentifier $completedIdentifier | Out-Null
    Write-SpeechJsonEvent -EventName "ready"

    $running = $true
    $readTask = [Console]::In.ReadLineAsync()
    while ($running) {
        Publish-PendingSpeechEvents `
            -StartedIdentifier $startedIdentifier `
            -CompletedIdentifier $completedIdentifier
        if (-not $readTask.IsCompleted) {
            Start-Sleep -Milliseconds 20
            continue
        }
        $line = $readTask.GetAwaiter().GetResult()
        if ($null -eq $line) {
            break
        }
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
                    $running = $false
                }
                default {
                    Write-SpeechJsonEvent -EventName "error" -Message "Unknown command"
                }
            }
        }
        catch {
            Write-SpeechJsonEvent -EventName "error" -Message $_.Exception.Message
        }
        if ($running) {
            $readTask = [Console]::In.ReadLineAsync()
        }
    }
    Unregister-Event -SourceIdentifier $startedIdentifier -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier $completedIdentifier -ErrorAction SilentlyContinue
    $synth.SpeakAsyncCancelAll()
    $synth.Dispose()
}
catch {
    Write-SpeechJsonEvent -EventName "error" -Message $_.Exception.Message
    exit 1
}
