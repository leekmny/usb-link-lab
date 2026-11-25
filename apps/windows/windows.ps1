[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("setup", "run", "probe", "test", "package", "smoke", "pairing-native")]
    [string] $Action,
    [string] $Archive,
    [string] $ConnectAlias,
    [switch] $ShowAliases
)

$ErrorActionPreference = "Stop"
$Project = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$PythonVersion = "3.12.16"
$Venv = Join-Path $PSScriptRoot ".venv"
$Python = Join-Path $Venv "Scripts/python.exe"
$Release = Join-Path $PSScriptRoot "dist/release"
$ZipName = "USBLinkLab-windows-x64.zip"
$NativePairing = Join-Path $PSScriptRoot "usblinklab/_native/ulab_pairing.dll"

function Assert-NativeSuccess([string] $Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step exited with code $LASTEXITCODE." }
}

function New-WorkDirectory {
    $directory = Join-Path ([IO.Path]::GetTempPath()) ("USBLinkLab-" + [guid]::NewGuid())
    return [IO.Directory]::CreateDirectory($directory).FullName
}

function Build-PairingNative {
    Get-Command uv -ErrorAction Stop | Out-Null
    $Source = Join-Path $Project "native/usb_pairing"
    $Mingw = $env:ULAB_MINGW_BIN
    if (-not $Mingw) {
        $Gcc = Get-Command gcc.exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($Gcc) {
            $Mingw = Split-Path $Gcc.Source -Parent
        } elseif ($env:SystemDrive) {
            $MsysUcrt = Join-Path "$($env:SystemDrive)/" "msys64/ucrt64/bin"
            if (Test-Path -LiteralPath (Join-Path $MsysUcrt "gcc.exe") -PathType Leaf) {
                $Mingw = $MsysUcrt
            }
        }
    }

    $Tools = @("--from", "cmake==3.31.10")
    $Compiler = "msvc"
    $Configure = @("-G", "Visual Studio 17 2022", "-A", "x64")
    $OriginalPath = $env:Path
    try {
        if ($Mingw) {
            $Gcc = (Resolve-Path -LiteralPath (Join-Path $Mingw "gcc.exe")).Path
            $Target = & $Gcc -dumpmachine
            Assert-NativeSuccess "MinGW compiler check"
            if ($Target -ne "x86_64-w64-mingw32") { throw "MinGW GCC must target x64 Windows." }
            $env:Path = "$Mingw;$env:Path"
            $Tools += @("--with", "ninja==1.13.0")
            $Compiler = "mingw"
            $Configure = @("-G", "Ninja", "-DCMAKE_C_COMPILER=$Gcc", "-DCMAKE_BUILD_TYPE=Release")
        }

        $Build = Join-Path $Project ".build/usb-pairing-windows-x64-$Compiler"
        uv tool run @Tools cmake -S $Source -B $Build @Configure
        Assert-NativeSuccess "Pairing native configuration"
        uv tool run @Tools cmake --build $Build --config Release --target ulab_pairing --parallel 4
        Assert-NativeSuccess "Pairing native build"
        Copy-Item -LiteralPath (Join-Path $Build "bin/Release/ulab_pairing.dll") -Destination $NativePairing -Force
    } finally {
        $env:Path = $OriginalPath
    }
}

if ($Action -eq "smoke") {
    if (-not $Archive) { $Archive = Join-Path $Release $ZipName }
    $Archive = (Resolve-Path -LiteralPath $Archive).Path
    $Checksum = Join-Path (Split-Path $Archive -Parent) "SHA256SUMS"
    $Expected = Get-Content -LiteralPath $Checksum | Where-Object {
        $_ -match '^[a-fA-F0-9]{64}  USBLinkLab-windows-x64\.zip$'
    }
    if (@($Expected).Count -ne 1 -or (Get-FileHash -LiteralPath $Archive).Hash -ne ($Expected -split " ")[0]) {
        throw "ZIP checksum mismatch."
    }

    $Work = New-WorkDirectory
    Expand-Archive -LiteralPath $Archive -DestinationPath $Work
    $Program = Join-Path $Work "USBLinkLab/USBLinkLab.exe"
    $ExpectedExe = Get-Content -LiteralPath $Checksum | Where-Object {
        $_ -match '^[a-fA-F0-9]{64}  USBLinkLab/USBLinkLab\.exe$'
    }
    if (@($ExpectedExe).Count -ne 1 -or (Get-FileHash -LiteralPath $Program).Hash -ne ($ExpectedExe -split " ")[0]) {
        throw "EXE checksum mismatch."
    }
    $Marker = Join-Path $Work "smoke.json"
    $OriginalPath = $env:Path
    $OriginalQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
        $env:QT_QPA_PLATFORM = "windows"
        $Launch = @{
            FilePath = $Program
            ArgumentList = @("--smoke-test", ('"' + $Marker + '"'))
            WorkingDirectory = $Work
            PassThru = $true
            WindowStyle = "Hidden"
        }
        $App = Start-Process @Launch
        if (-not $App.WaitForExit(45000)) {
            $App.Kill()
            throw "The portable application did not complete its smoke test in 45 seconds."
        }
        if ($App.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Marker -PathType Leaf)) {
            throw "The portable application failed its smoke test (exit $($App.ExitCode))."
        }
        $Smoke = Get-Content -Raw -LiteralPath $Marker | ConvertFrom-Json
        if ($Smoke.app -ne "USBLinkLab" -or $Smoke.status -ne "passed" -or
                $Smoke.frozen -ne $true -or $Smoke.platform -ne "windows" -or
                $Smoke.protocol -ne "ULAB1-20261124-R1" -or $Smoke.pairing_backend -ne "mbedtls-ecjpake") {
            throw "The portable app did not verify Windows Qt, the canonical protocol, and native pairing."
        }
        Write-Output "Native portable application smoke test passed."
        Get-Content -LiteralPath $Marker
    } finally {
        $env:Path = $OriginalPath
        $env:QT_QPA_PLATFORM = $OriginalQtPlatform
    }
    return
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT -or
        $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "Run this workflow on native Windows x64 using x64 PowerShell."
}

if ($Action -eq "pairing-native") {
    Build-PairingNative
    Write-Output $NativePairing
    return
}

if ($Action -eq "setup") {
    Get-Command uv -ErrorAction Stop | Out-Null
    uv python install $PythonVersion
    Assert-NativeSuccess "CPython installation"
    if (-not (Test-Path -LiteralPath $Python)) {
        uv venv --managed-python --python $PythonVersion $Venv
        Assert-NativeSuccess "Virtual environment setup"
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Run apps/windows/windows.ps1 setup first."
}
$InstalledPython = & $Python -c "import platform; print(platform.python_version())"
Assert-NativeSuccess "Interpreter version check"
if ($InstalledPython -ne $PythonVersion) { throw "The virtual environment must use CPython $PythonVersion." }

if ($Action -eq "setup") {
    $LockedWheels = Join-Path $PSScriptRoot "requirements-win.lock"
    uv pip sync --require-hashes --only-binary ':all:' --default-index 'https://pypi.org/simple' --python $Python $LockedWheels
    Assert-NativeSuccess "Locked wheel installation"
    uv pip check --python $Python
    Assert-NativeSuccess "Wheel compatibility check"
    Build-PairingNative
    return
}

switch ($Action) {
    "run" {
        & $Python (Join-Path $PSScriptRoot "launch.py")
        Assert-NativeSuccess "USBLinkLab"
    }
    "probe" {
        $ProbeArguments = @()
        if ($ShowAliases) { $ProbeArguments += "--show-aliases" }
        if ($ConnectAlias) { $ProbeArguments += @("--connect", $ConnectAlias) }
        & $Python (Join-Path $PSScriptRoot "probe_usb.py") @ProbeArguments
        exit $LASTEXITCODE
    }
    "test" {
        Build-PairingNative
        & $Python -m pytest (Join-Path $PSScriptRoot "tests") -q
        Assert-NativeSuccess "Windows tests"
    }
    "package" {
        Build-PairingNative
        $Commit = git -C $Project rev-parse HEAD
        Assert-NativeSuccess "Source revision check"
        $InitialChanges = @(git -C $Project status --porcelain --untracked-files=normal)
        Assert-NativeSuccess "Source status check"
        $Work = New-WorkDirectory
        $Dist = Join-Path $Work "dist"
        $Bundle = Join-Path $Dist "USBLinkLab"
        $OldHashSeed = $env:PYTHONHASHSEED
        try {
            $env:PYTHONHASHSEED = "0"
            $PyInstallerArguments = @(
                "--noconfirm", "--log-level", "WARN", "--distpath", $Dist,
                "--workpath", (Join-Path $Work "build"), (Join-Path $PSScriptRoot "USBLinkLab.spec")
            )
            & $Python -m PyInstaller @PyInstallerArguments
            Assert-NativeSuccess "PyInstaller packaging"
        } finally {
            $env:PYTHONHASHSEED = $OldHashSeed
        }

        $CurrentCommit = git -C $Project rev-parse HEAD
        Assert-NativeSuccess "Source revision recheck"
        $Changes = @(git -C $Project status --porcelain --untracked-files=normal)
        Assert-NativeSuccess "Source status check"
        $Canonical = Join-Path $Project "protocol/PROTOCOL_V1.md"
        $ProtocolHash = (Get-FileHash -LiteralPath $Canonical).Hash.ToLowerInvariant()
        if ($ProtocolHash -ne "535a818b4e76c82bf7c782f9aa9482730b13865e1ddb8c3fb0cba2c946a6596f") {
            throw "The canonical protocol file does not match ULAB1-20261124-R1."
        }
        $Program = Join-Path $Bundle "USBLinkLab.exe"
        $Signature = Get-AuthenticodeSignature -LiteralPath $Program
        & $Python (Join-Path $PSScriptRoot "package_notices.py") $Bundle
        Assert-NativeSuccess "License notice collection"
        Copy-Item -LiteralPath $Canonical -Destination (Join-Path $Bundle "PROTOCOL_V1.md")
        [ordered]@{
            source_commit = $Commit.Trim()
            source_working_tree_dirty = ($InitialChanges.Count -gt 0 -or $Changes.Count -gt 0 -or $Commit -ne $CurrentCommit)
            protocol_revision = "ULAB1-20261124-R1"
            protocol_sha256 = $ProtocolHash
            executable_signature = $Signature.Status.ToString()
            python = $InstalledPython
            architecture = "windows-x64"
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Bundle "BUILD-INFO.json") -Encoding ASCII

        New-Item -ItemType Directory -Force -Path $Release | Out-Null
        $Zip = Join-Path $Release $ZipName
        Compress-Archive -LiteralPath $Bundle -DestinationPath $Zip -CompressionLevel Optimal -Force
        $ZipHash = (Get-FileHash -LiteralPath $Zip).Hash.ToLowerInvariant()
        $ExeHash = (Get-FileHash -LiteralPath $Program).Hash.ToLowerInvariant()
        @("$ZipHash  $ZipName", "$ExeHash  USBLinkLab/USBLinkLab.exe") |
            Set-Content -LiteralPath (Join-Path $Release "SHA256SUMS") -Encoding ASCII
        Write-Output $Program
        Write-Output $Zip
    }
}
