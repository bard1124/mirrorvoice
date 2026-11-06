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

function Get-Zip($url, $dest) {
    $zip = Join-Path $env:TEMP ([IO.Path]::GetFileName($url))
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Expand-Archive -Path $zip -DestinationPath $dest -Force
}

foreach ($flavor in @("vulkan", "cpu")) {
    $dest = Join-Path $v "llama\$flavor"
    if (-not (Test-Path "$dest\llama-server.exe")) {
        Get-Zip "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA/llama-$LLAMA-bin-win-$flavor-x64.zip" $dest
        # some releases nest files in a build\bin folder; flatten
        Get-ChildItem $dest -Recurse -File | Where-Object { $_.DirectoryName -ne $dest } |
            ForEach-Object { Move-Item $_.FullName $dest -Force }
    }
}
if (-not (Test-Path "$v\uv.exe")) {
    Get-Zip "https://github.com/astral-sh/uv/releases/download/$UV/uv-x86_64-pc-windows-msvc.zip" "$v\uvtmp"
    Get-ChildItem "$v\uvtmp" -Recurse -Filter uv.exe | Select-Object -First 1 | Move-Item -Destination "$v\uv.exe" -Force
    Remove-Item "$v\uvtmp" -Recurse -Force
}
Get-ChildItem $v -Recurse -Filter *.exe | Select-Object FullName
