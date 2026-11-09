# Collect third-party binaries bundled into the Windows build (packaging\vendor\):
#   llama\vulkan\llama-server.exe  (GPU: NVIDIA / AMD / Intel via Vulkan)
#   llama\cpu\llama-server.exe     (fallback)
#   uv.exe                         (installs the optional CUDA "GPU voice pack" on demand)
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
$v = Join-Path $root "packaging\vendor"
New-Item -ItemType Directory -Force -Path $v | Out-Null

$LLAMA = "b12144"   # pinned llama.cpp release
$UV = "0.9.9"       # pinned uv release

function Save-ReleaseAsset($repo, $tag, $name, $out) {
    # The normal download link (github.com/<repo>/releases/download/...) sometimes answers 504 for
    # hours while api.github.com works: retry it, then fetch the same asset through the API.
    $url = "https://github.com/$repo/releases/download/$tag/$name"
    for ($i = 1; $i -le 3; $i++) {
        try { Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing; return }
        catch { Write-Host "download link attempt $i failed: $($_.Exception.Message)"; Start-Sleep -Seconds (5 * $i) }
    }
    $h = @{ "User-Agent" = "mirrorvoice-build" }
    if ($env:GITHUB_TOKEN) { $h["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/tags/$tag" -Headers $h
    $asset = $rel.assets | Where-Object { $_.name -eq $name } | Select-Object -First 1
    if (-not $asset) { throw "asset $name not found in $repo $tag" }
    $h["Accept"] = "application/octet-stream"
    Write-Host "fetching $name through the API"
    Invoke-WebRequest -Uri $asset.url -Headers $h -OutFile $out -UseBasicParsing
}

function Get-Zip($repo, $tag, $name, $dest) {
    $zip = Join-Path $env:TEMP $name
    Save-ReleaseAsset $repo $tag $name $zip
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Expand-Archive -Path $zip -DestinationPath $dest -Force
}

foreach ($flavor in @("vulkan", "cpu")) {
    $dest = Join-Path $v "llama\$flavor"
    if (-not (Test-Path "$dest\llama-server.exe")) {
        Get-Zip "ggml-org/llama.cpp" $LLAMA "llama-$LLAMA-bin-win-$flavor-x64.zip" $dest
        # some releases nest files in a build\bin folder; flatten
        Get-ChildItem $dest -Recurse -File | Where-Object { $_.DirectoryName -ne $dest } |
            ForEach-Object { Move-Item $_.FullName $dest -Force }
    }
}
if (-not (Test-Path "$v\uv.exe")) {
    Get-Zip "astral-sh/uv" $UV "uv-x86_64-pc-windows-msvc.zip" "$v\uvtmp"
    Get-ChildItem "$v\uvtmp" -Recurse -Filter uv.exe | Select-Object -First 1 | Move-Item -Destination "$v\uv.exe" -Force
    Remove-Item "$v\uvtmp" -Recurse -Force
}
Get-ChildItem $v -Recurse -Filter *.exe | Select-Object FullName
