[CmdletBinding()]
param(
    [string]$CameraProjectRoot = '',
    [string]$CudaRoot = ''
)

. (Join-Path $PSScriptRoot 'common.ps1')

$layout = Get-YuntongGpuLayout -CameraProjectRoot $CameraProjectRoot
Assert-ExactManagedPath -Candidate $layout.WorkRoot -Expected (Join-Path $layout.SoftwareRoot 'work\opencv-cuda') -Label 'OpenCV work root'
Assert-ExactManagedPath -Candidate $layout.GpuRuntimeRoot -Expected (Join-Path $layout.SoftwareRoot 'work\gpu-runtime') -Label 'GPU runtime root'

$venvPython = Get-VenvPython -Layout $layout
$null = Assert-64BitPython -PythonExe $venvPython
$cuda = Get-Cuda129Root -CudaRoot $CudaRoot
$null = Assert-Sm89Gpu

$env:CUDA_PATH = $cuda.Root
${env:CUDA_PATH_V12_9} = $cuda.Root
$env:PATH = "$(Join-Path $cuda.Root 'bin');$env:PATH"

$verifier = Join-Path $PSScriptRoot 'verify_gpu_stack.py'
Invoke-CheckedNative -FilePath $venvPython -Arguments @(
    $verifier,
    '--opencv-install-root',
    $layout.InstallRoot
) -FailureMessage 'GPU stack verification failed.'
