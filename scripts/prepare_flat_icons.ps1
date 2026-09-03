param(
    [string]$GeneratedRoot = "C:\Users\SubTree\.codex\generated_images\01a055e7-6450-7003-b078-a77b598c5c2e",
    [string]$OutputRoot = (Join-Path $PSScriptRoot "..\apps\miniprogram\addpicture\icons"),
    [string]$ContactSheetPath = (Join-Path $PSScriptRoot "..\artifacts\flat-icons.png")
)

Add-Type -AssemblyName System.Drawing

function Test-NeutralBackgroundPixel {
    param([System.Drawing.Color]$Color)
    if ($Color.A -le 8) { return $true }
    $maximum = [Math]::Max($Color.R, [Math]::Max($Color.G, $Color.B))
    $minimum = [Math]::Min($Color.R, [Math]::Min($Color.G, $Color.B))
    $average = ($Color.R + $Color.G + $Color.B) / 3
    return ($maximum - $minimum) -le 34 -and $average -ge 168
}

function Clear-Connected-NeutralBackground {
    param([System.Drawing.Bitmap]$Bitmap)

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
        if (-not (Test-NeutralBackgroundPixel -Color $color)) { continue }

        $Bitmap.SetPixel($x, $y, [System.Drawing.Color]::Transparent)
        if ($x -gt 0) { $queue.Enqueue($index - 1) }
        if ($x -lt ($width - 1)) { $queue.Enqueue($index + 1) }
        if ($y -gt 0) { $queue.Enqueue($index - $width) }
        if ($y -lt ($height - 1)) { $queue.Enqueue($index + $width) }
    }
}

function Keep-LargestOpaqueComponent {
    param([System.Drawing.Bitmap]$Bitmap)

    $width = $Bitmap.Width
    $height = $Bitmap.Height
    $visited = New-Object 'bool[]' ($width * $height)
    $largest = New-Object 'System.Collections.Generic.List[int]'

    for ($index = 0; $index -lt ($width * $height); $index++) {
        if ($visited[$index]) { continue }
        $x = $index % $width
        $y = [Math]::Floor($index / $width)
        if ($Bitmap.GetPixel($x, $y).A -le 12) {
            $visited[$index] = $true
            continue
        }

        $queue = New-Object 'System.Collections.Generic.Queue[int]'
        $component = New-Object 'System.Collections.Generic.List[int]'
        $queue.Enqueue($index)
        while ($queue.Count -gt 0) {
            $current = $queue.Dequeue()
            if ($visited[$current]) { continue }
            $visited[$current] = $true
            $currentX = $current % $width
            $currentY = [Math]::Floor($current / $width)
            if ($Bitmap.GetPixel($currentX, $currentY).A -le 12) { continue }
            $component.Add($current)
            if ($currentX -gt 0) { $queue.Enqueue($current - 1) }
            if ($currentX -lt ($width - 1)) { $queue.Enqueue($current + 1) }
            if ($currentY -gt 0) { $queue.Enqueue($current - $width) }
            if ($currentY -lt ($height - 1)) { $queue.Enqueue($current + $width) }
        }
        if ($component.Count -gt $largest.Count) { $largest = $component }
    }

    $keep = New-Object 'bool[]' ($width * $height)
    foreach ($index in $largest) { $keep[$index] = $true }
    for ($index = 0; $index -lt ($width * $height); $index++) {
        if ($keep[$index]) { continue }
        $x = $index % $width
        $y = [Math]::Floor($index / $width)
        $Bitmap.SetPixel($x, $y, [System.Drawing.Color]::Transparent)
    }
}

function Convert-ToFlatPalette {
    param(
        [System.Drawing.Bitmap]$Bitmap,
        [string]$Style
    )

    $source = $Bitmap.Clone()
    try {
        $blue = [System.Drawing.Color]::FromArgb(255, 47, 105, 236)
        $green = [System.Drawing.Color]::FromArgb(255, 32, 168, 98)
        $avatarFill = [System.Drawing.Color]::FromArgb(255, 221, 226, 234)
        $avatarLine = [System.Drawing.Color]::FromArgb(255, 105, 115, 130)
        $white = [System.Drawing.Color]::White
        $fill = if ($Style -eq 'avatar') { $avatarFill } elseif ($Style -in @('success', 'success-circle')) { $green } else { $blue }

        $graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
        try {
            $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $brush = New-Object System.Drawing.SolidBrush $fill
            try {
                if ($Style -eq 'avatar' -or $Style -eq 'success-circle') {
                    $graphics.FillEllipse($brush, 8, 8, 176, 176)
                }
                else {
                    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
                    try {
                        $radius = 34
                        $diameter = $radius * 2
                        $path.AddArc(8, 8, $diameter, $diameter, 180, 90)
                        $path.AddArc(184 - $diameter, 8, $diameter, $diameter, 270, 90)
                        $path.AddArc(184 - $diameter, 184 - $diameter, $diameter, $diameter, 0, 90)
                        $path.AddArc(8, 184 - $diameter, $diameter, $diameter, 90, 90)
                        $path.CloseFigure()
                        $graphics.FillPath($brush, $path)
                    }
                    finally { $path.Dispose() }
                }
            }
            finally { $brush.Dispose() }
        }
        finally { $graphics.Dispose() }

        for ($y = 18; $y -lt 174; $y++) {
            for ($x = 18; $x -lt 174; $x++) {
                $color = $source.GetPixel($x, $y)
                if ($color.A -le 12) { continue }
                if ($Style -eq 'avatar') {
                    $brightness = (0.299 * $color.R) + (0.587 * $color.G) + (0.114 * $color.B)
                    if ($brightness -lt 165) {
                        $Bitmap.SetPixel($x, $y, $avatarLine)
                    }
                    continue
                }

                $maximum = [Math]::Max($color.R, [Math]::Max($color.G, $color.B))
                $minimum = [Math]::Min($color.R, [Math]::Min($color.G, $color.B))
                $isWhiteLine = $minimum -ge 235 -and ($maximum - $minimum) -le 28
                if ($isWhiteLine) { $Bitmap.SetPixel($x, $y, $white) }
            }
        }
    }
    finally { $source.Dispose() }
}

$iconSources = [ordered]@{
    "lab"            = @{ File = "exec-0521a05c-8c6b-4c26-a4d5-ca6e2f559c0a.png"; Style = "blue" }
    "hospital"       = @{ File = "exec-95c000aa-510b-4ce1-b129-adc961feb18d.png"; Style = "blue" }
    "phone"          = @{ File = "exec-b4c1222f-bd04-4dcb-80f8-cda118303f1e.png"; Style = "blue" }
    "lock"           = @{ File = "exec-9612dc8b-2312-4d33-aa93-12eecdc9bac6.png"; Style = "blue" }
    "search"         = @{ File = "exec-9418d414-a599-47e1-8bf3-cb07692b662c.png"; Style = "blue" }
    "calendar"       = @{ File = "exec-695bced1-c4fd-40dc-934e-ec1ca088b265.png"; Style = "blue" }
    "clock"          = @{ File = "exec-c52cb70b-1ac8-41cf-9ffc-08ae349da485.png"; Style = "blue" }
    "location"       = @{ File = "exec-762ed04b-5b98-4781-98f5-73fd598a5c9e.png"; Style = "blue" }
    "route"          = @{ File = "exec-064d4dba-971d-4fd8-b920-572aca0347a9.png"; Style = "blue" }
    "queue"          = @{ File = "exec-28b6f0ca-9295-47cc-8834-b7979a76c748.png"; Style = "blue" }
    "info"           = @{ File = "exec-34d76be1-a59d-4c2b-8f70-a33ab943dda1.png"; Style = "blue" }
    "bell"           = @{ File = "exec-04556c5b-891d-435b-9498-1aae5931ca2d.png"; Style = "blue" }
    "wechat"         = @{ File = "exec-7bcbaa30-4ff8-4eef-84bf-099aef94c180.png"; Style = "blue" }
    "plus"           = @{ File = "exec-8a75395e-5d8a-4cb6-a14a-6894cfeda3c9.png"; Style = "blue" }
    "direction"      = @{ File = "exec-01f7f853-529b-411c-ae2a-210dda189868.png"; Style = "blue" }
    "imaging"        = @{ File = "exec-1df01f55-ca41-41c7-82ad-eac7b4a321b8.png"; Style = "blue" }
    "ultrasound"     = @{ File = "exec-0cc68755-e48b-41eb-9bca-90190777e230.png"; Style = "blue" }
    "ecg"            = @{ File = "exec-aaae685b-4e67-4b04-b2b8-0234eb3019dd.png"; Style = "blue" }
    "consultation"   = @{ File = "exec-175fb8e6-e999-42ca-a787-3bb3e5de828f.png"; Style = "blue" }
    "eye"            = @{ File = "exec-72845248-df17-4f96-b807-d2afb36e10cc.png"; Style = "blue" }
    "tooth"          = @{ File = "exec-9456a049-35b2-41fc-a782-79ca0632b86a.png"; Style = "blue" }
    "stomach"        = @{ File = "exec-e7b2d4e8-effa-41f2-8d84-66d7a5efac8f.png"; Style = "blue" }
    "id-card"        = @{ File = "exec-dedc0d19-7ef7-43c2-ba24-6c903bb7d9b3.png"; Style = "blue" }
    "water"          = @{ File = "exec-807428f3-4375-491e-bac0-e302bcdc390b.png"; Style = "blue" }
    "pill"           = @{ File = "exec-66512ed0-9755-4c39-8c43-ff9f74473d3f.png"; Style = "blue" }
    "shirt"          = @{ File = "exec-99093edc-a173-4cbf-ab7f-01a1167c6fbd.png"; Style = "blue" }
    "urine"          = @{ File = "exec-4f1861aa-f780-4d5c-b6d1-15f68fcaf1ac.png"; Style = "blue" }
    "record"         = @{ File = "exec-fb243693-a131-42f5-baf4-92d94f64baab.png"; Style = "blue" }
    "check"          = @{ File = "exec-a259f4ba-ce53-4245-8b35-6c98e7f48297.png"; Style = "success" }
    "success-circle" = @{ File = "exec-1fd755c8-f271-44ce-b6a7-0e64a53c8db2.png"; Style = "success-circle" }
    "user"           = @{ File = "exec-4296e215-dfed-4f51-8df8-f9fe58c0619c.png"; Style = "avatar" }
}

$outputDirectory = [System.IO.Path]::GetFullPath($OutputRoot)
$contactDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($ContactSheetPath))
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($contactDirectory) | Out-Null

$rendered = New-Object System.Collections.Generic.List[object]
foreach ($entry in $iconSources.GetEnumerator()) {
    $sourcePath = Join-Path $GeneratedRoot $entry.Value.File
    if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Missing generated icon: $sourcePath" }

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
            finally { $graphics.Dispose() }

            Clear-Connected-NeutralBackground -Bitmap $bitmap
            Convert-ToFlatPalette -Bitmap $bitmap -Style $entry.Value.Style
            $destination = Join-Path $outputDirectory ("icon-{0}.png" -f $entry.Key)
            $bitmap.Save($destination, [System.Drawing.Imaging.ImageFormat]::Png)
            $rendered.Add([pscustomobject]@{ Name = $entry.Key; Path = $destination })
        }
        finally { $bitmap.Dispose() }
    }
    finally { $source.Dispose() }
}

$columns = 6
$cellWidth = 230
$cellHeight = 250
$rows = [Math]::Ceiling($rendered.Count / $columns)
$sheet = New-Object System.Drawing.Bitmap ($columns * $cellWidth), ($rows * $cellHeight), ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
try {
    $graphics = [System.Drawing.Graphics]::FromImage($sheet)
    try {
        $graphics.Clear([System.Drawing.Color]::FromArgb(255, 244, 247, 251))
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
                try { $graphics.DrawImage($icon, $x, $y, 192, 192) }
                finally { $icon.Dispose() }
                $graphics.DrawString($rendered[$index].Name, $font, $brush, $x, $y + 202)
            }
        }
        finally {
            $font.Dispose()
            $brush.Dispose()
        }
    }
    finally { $graphics.Dispose() }
    $sheet.Save([System.IO.Path]::GetFullPath($ContactSheetPath), [System.Drawing.Imaging.ImageFormat]::Png)
}
finally { $sheet.Dispose() }

Write-Output ("Prepared {0} flat image-generated icons in {1}" -f $rendered.Count, $outputDirectory)
Write-Output ("Contact sheet: {0}" -f ([System.IO.Path]::GetFullPath($ContactSheetPath)))
