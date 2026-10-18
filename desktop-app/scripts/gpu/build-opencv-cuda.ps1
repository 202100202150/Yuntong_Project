[CmdletBinding()]
param(
    [string]$CameraProjectRoot = '',
    [string]$CudaRoot = '',
    [string]$CudaRuntimeRoot = '',
    [ValidateRange(1, 32)][int]$Jobs = 8
)

. (Join-Path $PSScriptRoot 'common.ps1')

$opencvVersion = '5.0.0'
$opencvCommit = '40738fb16ceddb5fb3fea747585f7ce6abb0605b'
$contribCommit = '755e50675d97db9b7d449d8bd6b09888646f6c6e'
$opencvUrl = 'https://github.com/opencv/opencv.git'
$contribUrl = 'https://github.com/opencv/opencv_contrib.git'

function Get-NormalizedGitUrl {
    param([string]$Url)
    ($Url.Trim().TrimEnd('/') -replace '\.git$', '').ToLowerInvariant()
}

function Ensure-PinnedGitSource {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Tag,
        [Parameter(Mandatory = $true)][string]$Commit,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Destination)) {
        Write-Host "Cloning $Name $Tag into $Destination"
        Invoke-CheckedNative -FilePath 'git.exe' -Arguments @(
            'clone', '--depth', '1', '--branch', $Tag, '--single-branch', $Url, $Destination
        ) -FailureMessage "Unable to clone $Name."
    }

    if (-not (Test-Path -LiteralPath (Join-Path $Destination '.git') -PathType Container)) {
        throw "'$Destination' exists but is not the managed $Name Git checkout. Refusing to overwrite it."
    }

    $actualRemote = (& git.exe -C $Destination config --get remote.origin.url).Trim()
    if ($LASTEXITCODE -ne 0 -or (Get-NormalizedGitUrl $actualRemote) -ne (Get-NormalizedGitUrl $Url)) {
        throw "$Name remote mismatch at '$Destination'. Expected '$Url', found '$actualRemote'."
    }

    $trackedChanges = (& git.exe -C $Destination status --porcelain --untracked-files=no) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the $Name checkout."
    }
    if (-not [string]::IsNullOrWhiteSpace($trackedChanges)) {
        throw "$Name contains tracked local changes. Preserve or revert them before using the managed build directory.`n$trackedChanges"
    }

    $actualCommit = (& git.exe -C $Destination rev-parse HEAD).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $Commit) {
        throw "$Name is not the audited $Tag commit. Expected $Commit, found $actualCommit. Remove only '$Destination' manually and rerun to acquire the pinned source."
    }
}

function ConvertTo-PythonSingleQuotedLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)
    # JSON string literals are also valid Python string literals and correctly
    # escape Windows path separators (for example, CUDA's "\\NVIDIA" segment).
    $Value | ConvertTo-Json -Compress
}

function Install-OpenCvVenvLink {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$CudaBin
    )

    $pythonInfo = (& $PythonExe -c "import json, sys, sysconfig; print(json.dumps({'site': sysconfig.get_paths()['purelib'], 'tag': sys.implementation.cache_tag.replace('cpython-', 'cp')}))") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to locate the GPU venv site-packages directory.'
    }

    $pydCandidates = @(Get-ChildItem -LiteralPath $InstallRoot -Recurse -File -Filter 'cv2*.pyd' |
        Where-Object { $_.Name -notmatch 'd\.pyd$' })
    if ($pydCandidates.Count -eq 0) {
        throw "OpenCV installation did not contain a cv2 extension under '$InstallRoot'."
    }
    $matchingPyd = @($pydCandidates | Where-Object { $_.Name -match [regex]::Escape($pythonInfo.tag) }) | Select-Object -First 1
    if ($null -eq $matchingPyd) {
        throw "No cv2 extension matched Python tag '$($pythonInfo.tag)'. Found: $($pydCandidates.Name -join ', ')"
    }

    $cv2Initializer = @(Get-ChildItem -LiteralPath $InstallRoot -Recurse -File -Filter '__init__.py' |
        Where-Object { $_.Directory.Name -eq 'cv2' }) | Select-Object -First 1
    if ($null -ne $cv2Initializer) {
        $opencvPythonPath = $cv2Initializer.Directory.Parent.FullName
    }
    else {
        # Some custom OpenCV configurations install only the extension module.
        $opencvPythonPath = $matchingPyd.DirectoryName
    }

    $opencvDllDirectories = @(Get-ChildItem -LiteralPath $InstallRoot -Recurse -File -Filter 'opencv_core*.dll' |
        Where-Object { $_.Name -notmatch 'd\.dll$' } |
        ForEach-Object { $_.DirectoryName } |
        Sort-Object -Unique)
    if ($opencvDllDirectories.Count -eq 0) {
        throw "OpenCV installation did not contain release DLLs under '$InstallRoot'."
    }

    $sitePackages = [IO.Path]::GetFullPath($pythonInfo.site)
    if (-not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
        throw "GPU venv site-packages does not exist: $sitePackages"
    }

    $dllDirectories = @($opencvDllDirectories + $CudaBin | Sort-Object -Unique)
    $pythonLiterals = @($dllDirectories | ForEach-Object { ConvertTo-PythonSingleQuotedLiteral $_ })
    $tupleBody = ($pythonLiterals | ForEach-Object { "    $_," }) -join "`r`n"
    $bootstrap = @"
import os

_DLL_HANDLES = []
for _directory in (
$tupleBody
):
    if os.path.isdir(_directory):
        _DLL_HANDLES.append(os.add_dll_directory(_directory))
"@

    $bootstrapPath = Join-Path $sitePackages 'yuntong_opencv_cuda_bootstrap.py'
    $pthPath = Join-Path $sitePackages 'yuntong_opencv_cuda.pth'
    Set-Content -LiteralPath $bootstrapPath -Value $bootstrap -Encoding UTF8
    Set-Content -LiteralPath $pthPath -Value @(
        'import yuntong_opencv_cuda_bootstrap'
        $opencvPythonPath
    ) -Encoding ASCII

    Write-Host "Linked CUDA OpenCV into the GPU venv: $($matchingPyd.FullName)"
}

$layout = Get-YuntongGpuLayout -CameraProjectRoot $CameraProjectRoot
Assert-ExactManagedPath -Candidate $layout.WorkRoot -Expected (Join-Path $layout.SoftwareRoot 'work\opencv-cuda') -Label 'OpenCV work root'
Assert-ExactManagedPath -Candidate $layout.GpuRuntimeRoot -Expected (Join-Path $layout.SoftwareRoot 'work\gpu-runtime') -Label 'GPU runtime root'

$venvPython = Get-VenvPython -Layout $layout
$pythonMetadata = Assert-64BitPython -PythonExe $venvPython
$cuda = Get-Cuda129Root -CudaRoot $CudaRoot
$gpu = Assert-Sm89Gpu

if ([string]::IsNullOrWhiteSpace($CudaRuntimeRoot)) {
    $CudaRuntimeRoot = Join-Path $layout.WorkRoot 'cuda-runtime-overlay'
}
$cudaRuntimeRoot = [IO.Path]::GetFullPath($CudaRuntimeRoot)
$cudaRuntimeInclude = Join-Path $cudaRuntimeRoot 'include'
$cudaRuntimeLib = Join-Path $cudaRuntimeRoot 'lib\x64'
if (-not (Test-Path -LiteralPath (Join-Path $cudaRuntimeInclude 'cuda_runtime.h') -PathType Leaf)) {
    throw "CUDA runtime development headers were not found at '$cudaRuntimeInclude'. Install the pinned nvidia-cuda-runtime-cu12 12.9.79 files into the project work overlay, or pass -CudaRuntimeRoot."
}
foreach ($requiredRuntimeLib in @('cudart.lib', 'cuda.lib', 'cudadevrt.lib')) {
    if (-not (Test-Path -LiteralPath (Join-Path $cudaRuntimeLib $requiredRuntimeLib) -PathType Leaf)) {
        throw "CUDA runtime development library '$requiredRuntimeLib' was not found at '$cudaRuntimeLib'."
    }
}
$cudaLegacyTypesHeader = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'cuda-legacy-types.h'))
if (-not (Test-Path -LiteralPath $cudaLegacyTypesHeader -PathType Leaf)) {
    throw "CUDA compatibility header was not found: $cudaLegacyTypesHeader"
}
$cudaCompatHeader = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'opencv_cuda_vec_traits_compat.h'))
if (-not (Test-Path -LiteralPath $cudaCompatHeader -PathType Leaf)) {
    throw "OpenCV CUDA compatibility header was not found: $cudaCompatHeader"
}
$null = Assert-CommandAvailable -Name 'git.exe'

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
    throw 'Visual Studio 2022 Build Tools were not found. Install the Desktop development with C++ workload.'
}
$vsJson = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($vsJson -join ''))) {
    throw 'Visual Studio 2022 C++ x64 build tools are missing.'
}
$vsInstances = @($vsJson | ConvertFrom-Json)
if ($vsInstances.Count -lt 1) {
    throw 'Visual Studio 2022 C++ x64 build tools are missing.'
}

# OpenCV probes the optional ASM language.  A MSYS2 `cc.exe` on PATH is not a
# suitable assembler for a Visual Studio generator and makes CMake select the
# Windows-GNU platform rules (which emit .a import libraries and -l/-L linker
# flags).  Prefer the native MSVC x64 assembler; if it is not installed,
# explicitly disable the optional probe rather than silently selecting MSYS2.
$vsToolsRoot = Join-Path $vsInstances[0].installationPath 'VC\Tools\MSVC'
$asmCandidates = @()
if (Test-Path -LiteralPath $vsToolsRoot -PathType Container) {
    $asmCandidates = @(Get-ChildItem -LiteralPath $vsToolsRoot -Recurse -File -Filter 'ml64.exe' -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\bin\\Hostx64\\x64\\ml64\.exe$' } |
        Sort-Object FullName -Descending)
}
$asmCompiler = if ($asmCandidates.Count -gt 0) {
    $asmCandidates[0].FullName -replace '\\', '/'
}
else {
    'NOTFOUND'
}

# Prefer the CMake shipped with the selected Visual Studio instance.  An MSYS2
# CMake earlier on PATH can parse Windows CUDA paths as escape sequences (for
# example ``C:\\Program Files``), causing OpenCV's legacy FindCUDA module to
# fail before configuration.  A caller may still override this with
# YUNTONG_CMAKE_EXE when a different native CMake is intentionally selected.
$cmakePath = $env:YUNTONG_CMAKE_EXE
if ([string]::IsNullOrWhiteSpace($cmakePath)) {
    $vsCmakeCandidate = Join-Path $vsInstances[0].installationPath 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
    if (Test-Path -LiteralPath $vsCmakeCandidate -PathType Leaf) {
        $cmakePath = $vsCmakeCandidate
    }
    else {
        $cmakePath = (Assert-CommandAvailable -Name 'cmake.exe').Source
    }
}
if (-not (Test-Path -LiteralPath $cmakePath -PathType Leaf)) {
    throw "CMake executable was not found: $cmakePath"
}
$cmakeVersionOutput = (& $cmakePath --version | Select-Object -First 1)
$cmakeVersionMatch = [regex]::Match(($cmakeVersionOutput -join "`n"), '(\d+\.\d+\.\d+)')
if (-not $cmakeVersionMatch.Success) {
    throw "Unable to determine the CMake version from '$cmakePath': $cmakeVersionOutput"
}
$cmakeVersion = [version]$cmakeVersionMatch.Value
if ($cmakeVersion -lt [version]'3.28.0') {
    throw "CMake 3.28 or newer is required. Found $cmakeVersion."
}

$forbidden = & $venvPython -c "import importlib.metadata as m, json; names={'opencv-python','opencv-contrib-python','opencv-python-headless','opencv-contrib-python-headless'}; print(json.dumps(sorted(d.metadata['Name'] for d in m.distributions() if str(d.metadata.get('Name','')).lower() in names)))"
if ($LASTEXITCODE -ne 0 -or $forbidden.Trim() -ne '[]') {
    throw "The isolated GPU venv must not contain a PyPI OpenCV wheel. Found: $forbidden"
}

$pythonBuildJson = & $venvPython -c "import json, os, sys, sysconfig, numpy; library=os.path.join(sys.base_prefix, 'libs', f'python{sys.version_info.major}{sys.version_info.minor}.lib'); print(json.dumps({'executable':sys.executable,'include':sysconfig.get_paths()['include'],'library':library,'numpy':numpy.get_include()}))"
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect the Python/NumPy build paths. Run setup-gpu-env.ps1 first.'
}
$pythonBuild = $pythonBuildJson | ConvertFrom-Json
foreach ($requiredPath in @($pythonBuild.include, $pythonBuild.library, $pythonBuild.numpy)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required Python build input not found: $requiredPath"
    }
}

New-Item -ItemType Directory -Force -Path $layout.SourceRoot, $layout.BuildRoot, $layout.InstallRoot, $layout.TempRoot, $layout.LogRoot | Out-Null
$opencvSource = Join-Path $layout.SourceRoot 'opencv'
$contribSource = Join-Path $layout.SourceRoot 'opencv_contrib'
Ensure-PinnedGitSource -Name 'opencv' -Url $opencvUrl -Tag $opencvVersion -Commit $opencvCommit -Destination $opencvSource
Ensure-PinnedGitSource -Name 'opencv_contrib' -Url $contribUrl -Tag $opencvVersion -Commit $contribCommit -Destination $contribSource

$driveName = ([IO.Path]::GetPathRoot($layout.WorkRoot)).TrimEnd('\').TrimEnd(':')
$drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
if ($null -ne $drive -and $drive.Free -lt 20GB) {
    throw "At least 20 GB free is required on drive $driveName for a repeatable OpenCV CUDA build. Free bytes: $($drive.Free)."
}

# OpenCV 5.0's legacy FindCUDA parser treats backslashes in CMake PATHS as
# escape sequences (notably ``\P`` in ``Program Files``).  Use equivalent
# forward-slash spellings for configuration and environment discovery; the
# native tools accept these paths on Windows and the actual layout is unchanged.
$cudaCmakeRoot = $cuda.Root -replace '\\', '/'
$cudaRuntimeCmakeRoot = $cudaRuntimeRoot -replace '\\', '/'
$cudaRuntimeCmakeInclude = $cudaRuntimeInclude -replace '\\', '/'
$cudaRuntimeCmakeLib = $cudaRuntimeLib -replace '\\', '/'
$cudaLegacyTypesCmake = $cudaLegacyTypesHeader -replace '\\', '/'
$cudaCompatHeaderCmake = $cudaCompatHeader -replace '\\', '/'
$env:TEMP = $layout.TempRoot
$env:TMP = $layout.TempRoot
$env:CUDA_PATH = $cudaCmakeRoot
${env:CUDA_PATH_V12_9} = $cudaCmakeRoot
# FindCUDA searches headers and libraries through these legacy variables.  The
# system CUDA installation on this machine has nvcc but omits the runtime SDK;
# point the searches at the project-local, version-matched overlay.
$env:CUDA_INC_PATH = $cudaRuntimeCmakeRoot
$env:CUDA_LIB_PATH = $cudaRuntimeCmakeRoot
$env:PATH = "$(Join-Path $cuda.Root 'bin');$env:PATH"

$cmakeArguments = @(
    '-S', $opencvSource,
    '-B', $layout.BuildRoot,
    '-G', 'Visual Studio 17 2022',
    '-A', 'x64',
    "-DCMAKE_ASM_COMPILER=$asmCompiler",
    "-DCMAKE_INSTALL_PREFIX=$([IO.Path]::GetFullPath($layout.InstallRoot) -replace '\\', '/')",
    '-DCMAKE_CONFIGURATION_TYPES=Release',
    "-DCMAKE_CUDA_COMPILER=$($cuda.Nvcc -replace '\\', '/')",
    "-DCUDAToolkit_ROOT=$cudaCmakeRoot",
    "-DCUDA_TOOLKIT_ROOT_DIR=$cudaCmakeRoot",
    "-DCUDA_TOOLKIT_INCLUDE=$cudaRuntimeCmakeInclude",
    "-DCUDA_INCLUDE_DIRS=$cudaRuntimeCmakeInclude;$($cudaCmakeRoot)/include",
    "-DCUDA_CUDART_LIBRARY=$cudaRuntimeCmakeLib/cudart.lib",
    "-DCUDA_CUDA_LIBRARY=$cudaRuntimeCmakeLib/cuda.lib",
    "-DCUDA_cudadevrt_LIBRARY=$cudaRuntimeCmakeLib/cudadevrt.lib",
    "-DCUDA_cudart_static_LIBRARY=$cudaRuntimeCmakeLib/cudart_static.lib",
    "-DCUDA_NVCC_FLAGS=-include;$cudaLegacyTypesCmake;-include;$cudaCompatHeaderCmake",
    '-DCMAKE_CUDA_ARCHITECTURES=89',
    '-DCUDA_ARCH_BIN=8.9',
    '-DCUDA_ARCH_PTX=',
    "-DOPENCV_EXTRA_MODULES_PATH=$(Join-Path $contribSource 'modules')",
    # The runtime detector uses TensorRT directly; cv2.dnn is not part of the
    # production path.  Excluding it avoids OpenCV 5.0's vendored MLAS object
    # library link issue on MSVC while keeping all CUDA preview modules.
    '-DBUILD_LIST=core,imgproc,imgcodecs,highgui,videoio,video,python3,cudev,cudaarithm,cudaimgproc,cudawarping',
    '-DWITH_CUDA=ON',
    '-DWITH_CUBLAS=ON',
    '-DWITH_CUDNN=OFF',
    '-DOPENCV_DNN_CUDA=OFF',
    '-DWITH_NVCUVID=OFF',
    '-DWITH_NVCUVENC=OFF',
    '-DENABLE_FAST_MATH=ON',
    '-DCUDA_FAST_MATH=ON',
    '-DBUILD_SHARED_LIBS=ON',
    '-DBUILD_opencv_python3=ON',
    '-DBUILD_opencv_apps=OFF',
    '-DBUILD_TESTS=OFF',
    '-DBUILD_PERF_TESTS=OFF',
    '-DBUILD_EXAMPLES=OFF',
    '-DBUILD_JAVA=OFF',
    '-DINSTALL_C_EXAMPLES=OFF',
    '-DINSTALL_PYTHON_EXAMPLES=OFF',
    '-DOPENCV_ENABLE_NONFREE=OFF',
    '-DOPENCV_FORCE_3RDPARTY_BUILD=ON',
    '-DWITH_IPP=OFF',
    '-DWITH_TBB=OFF',
    '-DWITH_OPENCL=OFF',
    '-DWITH_FFMPEG=OFF',
    '-DWITH_MSMF=ON',
    "-DPYTHON3_EXECUTABLE=$([IO.Path]::GetFullPath($pythonBuild.executable) -replace '\\', '/')",
    "-DPYTHON3_INCLUDE_DIR=$([IO.Path]::GetFullPath($pythonBuild.include) -replace '\\', '/')",
    "-DPYTHON3_LIBRARY=$([IO.Path]::GetFullPath($pythonBuild.library) -replace '\\', '/')",
    "-DPYTHON3_NUMPY_INCLUDE_DIRS=$([IO.Path]::GetFullPath($pythonBuild.numpy) -replace '\\', '/')",
    "-DPYTHON3_PACKAGES_PATH=$([IO.Path]::GetFullPath((Join-Path $layout.InstallRoot 'python')) -replace '\\', '/')"
)

Write-Host "Configuring OpenCV $opencvVersion + opencv_contrib $opencvVersion for CUDA 12.9 / SM 8.9"
Invoke-CheckedNative -FilePath $cmakePath -Arguments $cmakeArguments -FailureMessage 'OpenCV CUDA configuration failed.'

$cachePath = Join-Path $layout.BuildRoot 'CMakeCache.txt'
if (-not (Test-Path -LiteralPath $cachePath -PathType Leaf)) {
    throw 'CMake did not produce CMakeCache.txt.'
}
$cache = Get-Content -LiteralPath $cachePath -Raw
foreach ($requiredSetting in @('WITH_CUDA:BOOL=ON', 'BUILD_opencv_python3:BOOL=ON')) {
    if ($cache -notmatch [regex]::Escape($requiredSetting)) {
        throw "CMake cache is missing required setting '$requiredSetting'."
    }
}
if ($cache -notmatch 'CMAKE_CUDA_ARCHITECTURES:[^=]*=89' -and $cache -notmatch 'CUDA_ARCH_BIN:[^=]*=8\.9') {
    throw 'CMake cache is missing the required CUDA architecture 8.9 (RTX 4070).'
}
foreach ($requiredCudaCache in @('CUDA_TOOLKIT_INCLUDE', 'CUDA_CUDART_LIBRARY')) {
    if ($cache -match "${requiredCudaCache}:[^=]*=.*NOTFOUND") {
        throw "CMake did not resolve required CUDA component '$requiredCudaCache'."
    }
}

Write-Host "Building and installing CUDA OpenCV with $Jobs parallel jobs"
Invoke-CheckedNative -FilePath $cmakePath -Arguments @(
    '--build', $layout.BuildRoot, '--config', 'Release', '--target', 'INSTALL', '--parallel', $Jobs.ToString()
) -FailureMessage 'OpenCV CUDA compilation or installation failed.'

Install-OpenCvVenvLink -PythonExe $venvPython -InstallRoot $layout.InstallRoot -CudaBin (Join-Path $cuda.Root 'bin')

$manifest = [ordered]@{
    opencvVersion = $opencvVersion
    opencvCommit = $opencvCommit
    opencvContribCommit = $contribCommit
    cudaToolkit = '12.9'
    cudaArchitecture = '8.9'
    python = $pythonMetadata.version
    gpu = $gpu
    installRoot = $layout.InstallRoot
    generatedAt = (Get-Date).ToUniversalTime().ToString('o')
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $layout.InstallRoot 'yuntong-build-manifest.json') -Encoding UTF8

$verifier = Join-Path $PSScriptRoot 'verify_gpu_stack.py'
Invoke-CheckedNative -FilePath $venvPython -Arguments @(
    $verifier, '--opencv-install-root', $layout.InstallRoot
) -FailureMessage 'The completed OpenCV/TensorRT GPU stack failed verification.'

Write-Host ''
Write-Host 'OpenCV CUDA build and GPU venv integration completed.' -ForegroundColor Green
Write-Host "Build/install workspace: $($layout.WorkRoot)"
Write-Host "GPU Python: $venvPython"
