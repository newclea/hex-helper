$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sourceRoot = Join-Path $workspace 'outputs\runtime\phase1-1e34e09eec811f249ea16732306117a0'
$outputRoot = Join-Path $workspace 'outputs\tmp\hud_selection_audit'
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$sources = @(
    @{ Name = 'stage1'; File = '1788027777370864_f184_0_RAW.png' },
    @{ Name = 'stage2'; File = '1788027994173176_f4859_1_RAW.png' },
    @{ Name = 'stage3'; File = '1788028379533412_f13082_2_RAW.png' }
)

foreach ($source in $sources) {
    $inputPath = Join-Path $sourceRoot $source.File
    $image = [System.Drawing.Image]::FromFile($inputPath)
    try {
        # Fixed HUD region only for this 2560x1600 real sample audit.
        $cropRect = [System.Drawing.Rectangle]::new(360, 1280, 360, 320)
        $crop = [System.Drawing.Bitmap]::new($cropRect.Width, $cropRect.Height)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($crop)
            try {
                $graphics.DrawImage(
                    $image,
                    [System.Drawing.Rectangle]::new(0, 0, $crop.Width, $crop.Height),
                    $cropRect,
                    [System.Drawing.GraphicsUnit]::Pixel
                )
            }
            finally {
                $graphics.Dispose()
            }
            $outputPath = Join-Path $outputRoot ($source.Name + '.png')
            $crop.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally {
            $crop.Dispose()
        }
    }
    finally {
        $image.Dispose()
    }
}

Get-ChildItem -LiteralPath $outputRoot -Filter '*.png' | Select-Object FullName, Length
