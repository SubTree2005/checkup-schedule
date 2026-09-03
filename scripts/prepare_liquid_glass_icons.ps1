param(
    [string]$GeneratedRoot = "C:\Users\SubTree\.codex\generated_images\01a055e7-6450-7003-b078-a77b598c5c2e",
    [string]$OutputRoot = (Join-Path $PSScriptRoot "..\apps\miniprogram\addpicture\icons"),
    [string]$ContactSheetPath = (Join-Path $PSScriptRoot "..\artifacts\liquid-glass-icons.png")
)

Add-Type -AssemblyName System.Drawing

function Clear-Connected-NeutralBackground {
    param([System.Drawing.Bitmap]$Bitmap)

    $width = $Bitmap.Width
    $height = $Bitmap.Height
    $visited = New-Object 'bool[]' ($width * $height)
    $queue = New-Object 'System.Collections.Generic.Queue[int]'

    function Test-BackgroundPixel([System.Drawing.Color]$color) {
        $maximum = [Math]::Max($color.R, [Math]::Max($color.G, $color.B))
        $minimum = [Math]::Min($color.R, [Math]::Min($color.G, $color.B))
        $average = ($color.R + $color.G + $color.B) / 3
        return $color.A -gt 0 -and ($maximum - $minimum) -le 30 -and $average -ge 180
    }

    for ($x = 0; $x -lt $width; $x++) {
        $queue.Enqueue($x)
        $queue.Enqueue((($height - 1) * $width) + $x)
    }
    for ($y = 1; $y -lt ($height - 1); $y++) {
        $queue.Enqueue($y * $width)
        $queue.Enqueue(($y * $width) + ($width - 1))
    }

    while ($queue.Count -gt 0) {
        $index = $queue.Dequeue()
        if ($visited[$index]) { continue }
        $visited[$index] = $true
        $x = $index % $width
        $y = [Math]::Floor($index / $width)
        $color = $Bitmap.GetPixel($x, $y)
        if (-not (Test-BackgroundPixel $color)) { continue }

        $Bitmap.SetPixel($x, $y, [System.Drawing.Color]::Transparent)
        if ($x -gt 0) { $queue.Enqueue($index - 1) }
        if ($x -lt ($width - 1)) { $queue.Enqueue($index + 1) }
        if ($y -gt 0) { $queue.Enqueue($index - $width) }
        if ($y -lt ($height - 1)) { $queue.Enqueue($index + $width) }
    }
}

$iconSources = [ordered]@{
    "lab"          = "exec-474f907b-2536-4c31-b3b1-c5340e180e60.png"
    "home"         = "exec-4eb9b73d-1ab7-4517-9c0e-9aad8eb3df2d.png"
    "record"       = "exec-f4d44e8a-d149-42e9-bcb6-713aa376cec1.png"
    "user"         = "exec-14ba8c09-e3fe-4eb5-a77d-287a077ed320.png"
    "phone"        = "exec-ff4f7578-e524-448e-8a09-a4718e27b226.png"
    "lock"         = "exec-4a0ddae3-fc81-4538-9a80-1400b6cc3263.png"
    "search"       = "exec-d3bfc648-5245-4351-8ef1-77d37a4a38c3.png"
    "hospital"     = "exec-1c1196ac-1b67-4ed5-affc-8ea32c009346.png"
    "calendar"     = "exec-fbbaf47a-09e7-4ce8-a781-80402732824d.png"
    "clock"        = "exec-54c4a59c-e835-4918-af83-ae63f7972072.png"
    "location"     = "exec-d8c494bb-8d0a-4120-9d75-0427a92a9546.png"
    "route"        = "exec-75a9ca94-2d67-4e52-b87f-594c7a44c3b7.png"
    "queue"        = "exec-11fc8c14-6cff-4835-858c-2c52292114fb.png"
    "info"         = "exec-ab03193e-67e8-4d49-8654-3451fd74e0db.png"
    "bell"         = "exec-e4d90cce-78df-47ae-8466-f4ef653d9a67.png"
    "wechat"       = "exec-12b34121-e855-4334-be07-64c578891ac6.png"
    "check"        = "exec-5177c4cb-08fa-421b-8063-f9a8a5b4dde9.png"
    "plus"         = "exec-b97027fc-4864-4c4a-ad02-032745458d41.png"
    "direction"    = "exec-b80e27a6-dd76-4e81-9a6d-c6f21d20b257.png"
    "imaging"      = "exec-32bc8800-fe9a-4e97-8512-dbbdd48f38be.png"
    "ultrasound"   = "exec-acb8443c-de8b-4e02-8eba-20b146e85a0c.png"
    "ecg"          = "exec-0d00f9ae-db59-4bad-8755-53f43034f101.png"
    "consultation" = "exec-23f1ad6f-8e3a-4a92-a3ba-dcce6638cf87.png"
    "eye"          = "exec-ae4a07cf-fd7b-4fc8-8713-261bf43fc012.png"
    "tooth"        = "exec-a67ebc65-6274-427a-802c-a4ba4e73996c.png"
    "stomach"      = "exec-39185eeb-93a1-4d9e-aa53-3aa1e21802c0.png"
    "id-card"      = "exec-3dac829d-94e1-433f-bf2f-fc5a79b76b3c.png"
    "water"        = "exec-87491a53-e9f9-4e3b-8eef-dc192a08620b.png"
    "pill"         = "exec-48423e02-a8af-4054-b73a-f3b2331fea3c.png"
    "shirt"        = "exec-d0972cb9-ae30-49cb-be73-66f8924a679e.png"
    "urine"        = "exec-6b596590-9ee7-4449-a776-861a0da34181.png"
}

$outputDirectory = [System.IO.Path]::GetFullPath($OutputRoot)
$contactDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($ContactSheetPath))
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($contactDirectory) | Out-Null

$rendered = New-Object System.Collections.Generic.List[object]
foreach ($entry in $iconSources.GetEnumerator()) {
    $sourcePath = Join-Path $GeneratedRoot $entry.Value
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Missing generated icon: $sourcePath"
    }

    $source = [System.Drawing.Image]::FromFile($sourcePath)
    try {
        $bitmap = New-Object System.Drawing.Bitmap 192, 192, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
                $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
                $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
                $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
                $graphics.Clear([System.Drawing.Color]::Transparent)
                $graphics.DrawImage($source, 0, 0, 192, 192)
            }
            finally {
                $graphics.Dispose()
            }

            $destination = Join-Path $outputDirectory ("icon-{0}.png" -f $entry.Key)
            Clear-Connected-NeutralBackground -Bitmap $bitmap
            $bitmap.Save($destination, [System.Drawing.Imaging.ImageFormat]::Png)
            $rendered.Add([pscustomobject]@{ Name = $entry.Key; Path = $destination })
        }
        finally {
            $bitmap.Dispose()
        }
    }
    finally {
        $source.Dispose()
    }
}

$columns = 6
$cellWidth = 230
$cellHeight = 250
$rows = [Math]::Ceiling($rendered.Count / $columns)
$sheet = New-Object System.Drawing.Bitmap ($columns * $cellWidth), ($rows * $cellHeight), ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
try {
    $graphics = [System.Drawing.Graphics]::FromImage($sheet)
    try {
        $graphics.Clear([System.Drawing.Color]::FromArgb(255, 242, 246, 252))
        $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
        $font = New-Object System.Drawing.Font "Segoe UI", 15, ([System.Drawing.FontStyle]::Regular), ([System.Drawing.GraphicsUnit]::Pixel)
        $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 25, 42, 67))
        try {
            for ($index = 0; $index -lt $rendered.Count; $index++) {
                $column = $index % $columns
                $row = [Math]::Floor($index / $columns)
                $x = ($column * $cellWidth) + 19
                $y = ($row * $cellHeight) + 14
                $icon = [System.Drawing.Image]::FromFile($rendered[$index].Path)
                try {
                    $graphics.DrawImage($icon, $x, $y, 192, 192)
                }
                finally {
                    $icon.Dispose()
                }
                $graphics.DrawString($rendered[$index].Name, $font, $brush, $x, $y + 202)
            }
        }
        finally {
            $font.Dispose()
            $brush.Dispose()
        }
    }
    finally {
        $graphics.Dispose()
    }
    $sheet.Save([System.IO.Path]::GetFullPath($ContactSheetPath), [System.Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $sheet.Dispose()
}

Write-Output ("Prepared {0} icons in {1}" -f $rendered.Count, $outputDirectory)
Write-Output ("Contact sheet: {0}" -f ([System.IO.Path]::GetFullPath($ContactSheetPath)))
