Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-YuntongGpuLayout {
    param(
        [string]$CameraProjectRoot = ''
    )

    $softwareRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $workRoot = [IO.Path]::GetFullPath((Join-Path $softwareRoot 'work\opencv-cuda'))
    $gpuRuntimeRoot = [IO.Path]::GetFullPath((Join-Path $softwareRoot 'work\gpu-runtime'))

    if ([string]::IsNullOrWhiteSpace($CameraProjectRoot)) {
        if (-not [string]::IsNullOrWhiteSpace($env:YUNTONG_CAMERA_PROJECT_DIR)) {
            $CameraProjectRoot = $env:YUNTONG_CAMERA_PROJECT_DIR
        }
        else {
            # Public source layout:
            # <repository>\desktop-app
            # <repository>\camera-rtsp-low-latency
            $monorepoCameraRoot = [IO.Path]::GetFullPath((Join-Path $softwareRoot '..\camera-rtsp-low-latency'))
            if (Test-Path -LiteralPath (Join-Path $monorepoCameraRoot 'requirements-gpu.txt') -PathType Leaf) {
                $CameraProjectRoot = $monorepoCameraRoot
            }
            else {
                $documents = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)
                $CameraProjectRoot = Join-Path $documents 'ChatGPT\yuntong\camera-rtsp-low-latency'
            }
        }
    }

    $cameraRoot = [IO.Path]::GetFullPath($CameraProjectRoot)
    [pscustomobject]@{
        SoftwareRoot       = $softwareRoot
        WorkRoot           = $workRoot
        SourceRoot         = Join-Path $workRoot 'src'
        BuildRoot          = Join-Path $workRoot 'build'
        InstallRoot        = Join-Path $workRoot 'install'
        TempRoot           = Join-Path $workRoot 'tmp'
        LogRoot            = Join-Path $workRoot 'logs'
        GpuRuntimeRoot     = $gpuRuntimeRoot
        GpuVenvRoot        = Join-Path $gpuRuntimeRoot '.venv'
        CameraProjectRoot  = $cameraRoot
        GpuRequirements    = Join-Path $cameraRoot 'requirements-gpu.txt'
    }
}

function Assert-ExactManagedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $candidateFull = [IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $expectedFull = [IO.Path]::GetFullPath($Expected).TrimEnd('\')
    if (-not $candidateFull.Equals($expectedFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must remain at '$expectedFull'; received '$candidateFull'."
    }
}

function Assert-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required command '$Name' was not found on PATH."
    }
    return $command
}

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @(),
        [Parameter(Mandatory = $false)][string]$FailureMessage = 'Native command failed.'
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Exit code: $LASTEXITCODE"
    }
}

function Get-VenvPython {
    param([Parameter(Mandatory = $true)]$Layout)

    Join-Path $Layout.GpuVenvRoot 'Scripts\python.exe'
}

function Assert-64BitPython {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [string]$ExpectedVersion = '3.12.13'
    )

    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Python executable not found: $PythonExe"
    }

    $metadataJson = & $PythonExe -c "import json, platform, sys; print(json.dumps({'version': platform.python_version(), 'bits': 64 if sys.maxsize > 2**32 else 32}))"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Python at '$PythonExe'."
    }
    $metadata = $metadataJson | ConvertFrom-Json
    if ($metadata.bits -ne 64) {
        throw "A 64-bit Python interpreter is required. Found $($metadata.bits)-bit."
    }

    if ($metadata.version -ne $ExpectedVersion) {
        throw "The GPU runtime is pinned to CPython $ExpectedVersion x64. Found $($metadata.version) at '$PythonExe'."
    }
    return $metadata
}

function Get-Cuda129Root {
    param([string]$CudaRoot = '')

    if ([string]::IsNullOrWhiteSpace($CudaRoot)) {
        if (-not [string]::IsNullOrWhiteSpace(${env:CUDA_PATH_V12_9})) {
            $CudaRoot = ${env:CUDA_PATH_V12_9}
        }
        else {
            $CudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9'
        }
    }

    $fullRoot = [IO.Path]::GetFullPath($CudaRoot)
    $nvcc = Join-Path $fullRoot 'bin\nvcc.exe'
    if (-not (Test-Path -LiteralPath $nvcc -PathType Leaf)) {
        throw "CUDA Toolkit 12.9 was not found at '$fullRoot'. Install the CUDA 12.9 Toolkit (not only the NVIDIA driver), open a new terminal, and retry."
    }

    $nvccOutput = (& $nvcc --version 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $nvccOutput -notmatch 'release\s+12\.9(?:\D|$)') {
        throw "Expected CUDA Toolkit 12.9, but nvcc reported:`n$nvccOutput"
    }
    return [pscustomobject]@{ Root = $fullRoot; Nvcc = $nvcc; VersionOutput = $nvccOutput }
}

function Assert-Sm89Gpu {
    $null = Assert-CommandAvailable -Name 'nvidia-smi.exe'
    $rows = & nvidia-smi.exe --query-gpu=name,compute_cap,driver_version --format=csv,noheader,nounits
    if ($LASTEXITCODE -ne 0 -or $null -eq $rows) {
        throw 'nvidia-smi could not query the NVIDIA GPU.'
    }

    $matchingRow = @($rows | Where-Object { $_ -match ',\s*8\.9\s*,' }) | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($matchingRow)) {
        throw "This build is pinned to SM 8.9 (RTX 4070). nvidia-smi reported: $($rows -join '; ')"
    }
    return $matchingRow.Trim()
}
