param(
    [string]$GeneratedRoot = "C:\Users\SubTree\.codex\generated_images\01a055e7-6450-7003-b078-a77b598c5c2e",
    [string]$OutputRoot = (Join-Path $PSScriptRoot "..\apps\miniprogram\addpicture\icons"),
    [string]$ContactSheetPath = (Join-Path $PSScriptRoot "..\artifacts\colored-line-icons.png")
)

Add-Type -AssemblyName System.Drawing

function Test-NeutralEdgePixel([System.Drawing.Color]$Color) {
    if ($Color.A -le 8) { return $true }
    $maximum = [Math]::Max($Color.R, [Math]::Max($Color.G, $Color.B))
    $minimum = [Math]::Min($Color.R, [Math]::Min($Color.G, $Color.B))
    $average = ($Color.R + $Color.G + $Color.B) / 3
    return ($maximum - $minimum) -le 26 -and $average -ge 225
}

function Clear-Connected-NeutralBackground([System.Drawing.Bitmap]$Bitmap) {
    $width = $Bitmap.Width
    $height = $Bitmap.Height
    $visited = New-Object 'bool[]' ($width * $height)
    $queue = New-Object 'System.Collections.Generic.Queue[int]'
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
        if (-not (Test-NeutralEdgePixel $color)) { continue }
        $Bitmap.SetPixel($x, $y, [System.Drawing.Color]::Transparent)
        if ($x -gt 0) { $queue.Enqueue($index - 1) }
        if ($x -lt ($width - 1)) { $queue.Enqueue($index + 1) }
        if ($y -gt 0) { $queue.Enqueue($index - $width) }
        if ($y -lt ($height - 1)) { $queue.Enqueue($index + $width) }
    }
}

function Find-OpaqueBounds([System.Drawing.Bitmap]$Bitmap) {
    $left = $Bitmap.Width
    $top = $Bitmap.Height
    $right = -1
    $bottom = -1
    for ($y = 0; $y -lt $Bitmap.Height; $y++) {
        for ($x = 0; $x -lt $Bitmap.Width; $x++) {
            if ($Bitmap.GetPixel($x, $y).A -le 12) { continue }
            $left = [Math]::Min($left, $x)
            $top = [Math]::Min($top, $y)
            $right = [Math]::Max($right, $x)
            $bottom = [Math]::Max($bottom, $y)
        }
    }
    if ($right -lt $left) { return [System.Drawing.Rectangle]::new(0, 0, $Bitmap.Width, $Bitmap.Height) }
    return [System.Drawing.Rectangle]::new($left, $top, $right - $left + 1, $bottom - $top + 1)
}

$sources = [ordered]@{
    "clock-art"      = "exec-98c9ba11-42e9-49d9-89d7-843a14ba9bf5.png"
    "calendar-color" = "exec-1f8c2d3c-1d13-4844-a398-389e6a5b2398.png"
    "clock-color"    = "exec-1c4e40b1-95d0-4539-afad-50a4df5d7024.png"
    "stomach-color"  = "exec-59ec2c94-f236-48e0-a5d8-08147e4f1e3b.png"
    "id-card-color"  = "exec-e4d3643e-99fb-465a-9b11-4cacb39689f1.png"
    "water-color"    = "exec-a1be5ac3-71dc-411a-b5d3-c9e210dbb566.png"
    "pill-color"     = "exec-75e0e09f-fa49-4529-9b16-575d265bf955.png"
    "shirt-color"    = "exec-9a90ce3a-c99f-40b7-9f6d-21f59f5fa658.png"
    "wechat-color"   = "exec-28ff2782-f228-4f37-b9ae-5762f5afc310.png"
    "bell-color"     = "exec-63b5ac3c-55f6-424b-866d-74af512186dd.png"
}

$outputDirectory = [System.IO.Path]::GetFullPath($OutputRoot)
$contactDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($ContactSheetPath))
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($contactDirectory) | Out-Null
$rendered = New-Object System.Collections.Generic.List[object]

foreach ($entry in $sources.GetEnumerator()) {
    $sourcePath = Join-Path $GeneratedRoot $entry.Value
    if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Missing generated icon: $sourcePath" }
    $original = [System.Drawing.Image]::FromFile($sourcePath)
    try {
        $source = New-Object System.Drawing.Bitmap 320, 320, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $sourceGraphics = [System.Drawing.Graphics]::FromImage($source)
        try {
            $sourceGraphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $sourceGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $sourceGraphics.Clear([System.Drawing.Color]::Transparent)
            $sourceGraphics.DrawImage($original, 0, 0, 320, 320)
        }
        finally { $sourceGraphics.Dispose() }
        try {
            Clear-Connected-NeutralBackground $source
            $bounds = Find-OpaqueBounds $source
        $canvas = New-Object System.Drawing.Bitmap 192, 192, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($canvas)
            try {
                $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
                $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
                $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
                $graphics.Clear([System.Drawing.Color]::Transparent)
                $scale = [Math]::Min(160.0 / $bounds.Width, 160.0 / $bounds.Height)
                $width = [Math]::Max(1, [Math]::Round($bounds.Width * $scale))
                $height = [Math]::Max(1, [Math]::Round($bounds.Height * $scale))
                $destination = [System.Drawing.Rectangle]::new([Math]::Round((192 - $width) / 2), [Math]::Round((192 - $height) / 2), $width, $height)
                $graphics.DrawImage($source, $destination, $bounds, [System.Drawing.GraphicsUnit]::Pixel)
            }
            finally { $graphics.Dispose() }
            $outputPath = Join-Path $outputDirectory ("icon-{0}.png" -f $entry.Key)
            $canvas.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
            $rendered.Add([pscustomobject]@{ Name = $entry.Key; Path = $outputPath })
        }
        finally { $canvas.Dispose() }
        }
        finally { $source.Dispose() }
    }
    finally { $original.Dispose() }
}

$sheet = New-Object System.Drawing.Bitmap 960, 460, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
try {
    $graphics = [System.Drawing.Graphics]::FromImage($sheet)
    try {
        $graphics.Clear([System.Drawing.Color]::FromArgb(255, 246, 248, 252))
        $font = New-Object System.Drawing.Font "Segoe UI", 13
        $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 40, 51, 70))
        try {
            for ($index = 0; $index -lt $rendered.Count; $index++) {
                $x = ($index % 5) * 192
                $y = [Math]::Floor($index / 5) * 230
                $icon = [System.Drawing.Image]::FromFile($rendered[$index].Path)
                try { $graphics.DrawImage($icon, $x + 16, $y + 6, 160, 160) }
                finally { $icon.Dispose() }
                $graphics.DrawString($rendered[$index].Name, $font, $brush, $x + 14, $y + 178)
            }
        }
        finally { $font.Dispose(); $brush.Dispose() }
    }
    finally { $graphics.Dispose() }
    $sheet.Save([System.IO.Path]::GetFullPath($ContactSheetPath), [System.Drawing.Imaging.ImageFormat]::Png)
}
finally { $sheet.Dispose() }

Write-Output "Prepared $($rendered.Count) colored line assets."
Write-Output ([System.IO.Path]::GetFullPath($ContactSheetPath))
