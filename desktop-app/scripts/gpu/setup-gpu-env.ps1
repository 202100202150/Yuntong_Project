[CmdletBinding()]
param(
    [string]$CameraProjectRoot = '',
    [string]$BasePython = ''
)

. (Join-Path $PSScriptRoot 'common.ps1')

$layout = Get-YuntongGpuLayout -CameraProjectRoot $CameraProjectRoot
Assert-ExactManagedPath -Candidate $layout.GpuRuntimeRoot -Expected (Join-Path $layout.SoftwareRoot 'work\gpu-runtime') -Label 'GPU runtime root'

if (-not (Test-Path -LiteralPath $layout.CameraProjectRoot -PathType Container)) {
    throw "Camera project not found: $($layout.CameraProjectRoot)"
}
if (-not (Test-Path -LiteralPath $layout.GpuRequirements -PathType Leaf)) {
    throw "Pinned GPU requirements not found: $($layout.GpuRequirements)"
}

$cuda = Get-Cuda129Root
$gpu = Assert-Sm89Gpu

New-Item -ItemType Directory -Force -Path $layout.GpuRuntimeRoot | Out-Null
$pipTemp = Join-Path $layout.GpuRuntimeRoot 'tmp'
New-Item -ItemType Directory -Force -Path $pipTemp | Out-Null
$env:TEMP = $pipTemp
$env:TMP = $pipTemp
$env:UV_CACHE_DIR = Join-Path $layout.GpuRuntimeRoot 'uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $layout.GpuRuntimeRoot 'python'
$venvPython = Get-VenvPython -Layout $layout
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host "Creating isolated GPU venv at $($layout.GpuVenvRoot)"
    if (-not [string]::IsNullOrWhiteSpace($BasePython)) {
        $null = Assert-64BitPython -PythonExe $BasePython
        Invoke-CheckedNative -FilePath $BasePython -Arguments @('-m', 'venv', $layout.GpuVenvRoot) -FailureMessage 'Unable to create the GPU venv.'
    }
    else {
        $uv = Assert-CommandAvailable -Name 'uv.exe'
        Invoke-CheckedNative -FilePath $uv.Source -Arguments @('python', 'install', '3.12.13') -FailureMessage 'Unable to provision the pinned CPython 3.12.13 runtime with uv.'
        Invoke-CheckedNative -FilePath $uv.Source -Arguments @('venv', '--python', '3.12.13', '--seed', $layout.GpuVenvRoot) -FailureMessage 'Unable to create the pinned GPU venv with uv.'
    }
}
elseif (-not (Test-Path -LiteralPath (Join-Path $layout.GpuVenvRoot 'pyvenv.cfg') -PathType Leaf)) {
    throw "Refusing to reuse '$($layout.GpuVenvRoot)' because it is not a Python venv."
}

$venvMetadata = Assert-64BitPython -PythonExe $venvPython
if (-not [string]::IsNullOrWhiteSpace($BasePython)) {
    $baseMetadata = Assert-64BitPython -PythonExe $BasePython
    if ($venvMetadata.version -ne $baseMetadata.version) {
        throw "The existing GPU venv uses Python $($venvMetadata.version), but the selected base Python is $($baseMetadata.version). Remove only '$($layout.GpuVenvRoot)' manually if you intend to recreate it."
    }
}

$forbidden = & $venvPython -c "import importlib.metadata as m, json; names={'opencv-python','opencv-contrib-python','opencv-python-headless','opencv-contrib-python-headless'}; print(json.dumps(sorted(d.metadata['Name'] for d in m.distributions() if str(d.metadata.get('Name','')).lower() in names)))"
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect the GPU venv for conflicting OpenCV wheels.'
}
if ($forbidden.Trim() -ne '[]') {
    throw "The GPU venv contains a conflicting PyPI OpenCV wheel: $forbidden. Remove it from this isolated venv before linking the CUDA source build."
}

$pipCache = Join-Path $layout.GpuRuntimeRoot 'pip-cache'
New-Item -ItemType Directory -Force -Path $pipCache, $pipTemp | Out-Null
$env:TEMP = $pipTemp
$env:TMP = $pipTemp
$env:PIP_CACHE_DIR = $pipCache
$env:CUDA_PATH = $cuda.Root
${env:CUDA_PATH_V12_9} = $cuda.Root
$env:PATH = "$(Join-Path $cuda.Root 'bin');$env:PATH"

Write-Host "Installing exact GPU dependency pins from $($layout.GpuRequirements)"
Invoke-CheckedNative -FilePath $venvPython -Arguments @(
    '-m', 'pip', 'install',
    '--disable-pip-version-check',
    '--extra-index-url', 'https://pypi.nvidia.com',
    '--only-binary=:all:',
    '--upgrade-strategy', 'only-if-needed',
    '--requirement', $layout.GpuRequirements
) -FailureMessage 'Pinned GPU dependency installation failed.'

# TensorRT's CUDA 12 meta package supplies the `tensorrt` import shim.  It is
# source-only, so install it separately from the wheel-only dependency set;
# --no-deps keeps the exact bindings and runtime libraries pinned above.
Invoke-CheckedNative -FilePath $venvPython -Arguments @(
    '-m', 'pip', 'install',
    '--disable-pip-version-check',
    '--no-deps',
    '--extra-index-url', 'https://pypi.nvidia.com',
    'tensorrt-cu12==11.3.0.99'
) -FailureMessage 'TensorRT Python import package installation failed.'

Invoke-CheckedNative -FilePath $venvPython -Arguments @('-m', 'pip', 'check') -FailureMessage 'pip detected an inconsistent GPU environment.'

$verifier = Join-Path $PSScriptRoot 'verify_gpu_stack.py'
Invoke-CheckedNative -FilePath $venvPython -Arguments @($verifier, '--tensorrt-only') -FailureMessage 'TensorRT/CUDA Python verification failed.'

Write-Host ''
Write-Host 'GPU dependency environment is ready.' -ForegroundColor Green
Write-Host "Python: $venvPython"
Write-Host "Version: $($venvMetadata.version) (64-bit)"
Write-Host "GPU: $gpu"
Write-Host 'Next: run scripts\gpu\build-opencv-cuda.ps1 from the software root.'
