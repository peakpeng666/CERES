# Public Windows installer. No GitHub login and no docker login.
#   irm https://raw.githubusercontent.com/peakpeng666/URP/main/install.ps1 | iex
# Docker Desktop pulls ghcr.io/peakpeng666/urp-sim and does not clone the repository.
# Without Docker, the same bash installer runs inside WSL2 Ubuntu 22.04 x86_64.
# For another source, set $env:URP_REF before irm|iex, or use -Ref with a file.
# The download URL alone is not inherited by scripts invoked through iex.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'update', 'status', 'fuel')]
    [string]$Command = 'up',
    [string]$Ref = $env:URP_REF
)

$ErrorActionPreference = 'Stop'
$RawRoot = 'https://raw.githubusercontent.com/peakpeng666/URP'
$State = if ($env:URP_HOME) { $env:URP_HOME } else { Join-Path $env:LOCALAPPDATA 'urp' }
$saved = $null
$selectionPath = Join-Path $State 'install.selection.json'
if (Test-Path -LiteralPath $selectionPath) { $saved = Get-Content -Raw -LiteralPath $selectionPath -Encoding UTF8 | ConvertFrom-Json }
$explicitRef = $Ref
if (-not $Ref) { $Ref = if ($saved -and $saved.ref) { $saved.ref } else { 'main' } }
if ($env:URP_RAW -and $env:URP_RAW.StartsWith("$RawRoot/")) {
    $rawRef = $env:URP_RAW.Substring($RawRoot.Length + 1).TrimEnd('/')
    if ($explicitRef -and $explicitRef -ne $rawRef) { throw 'URP_RAW 与 URP_REF/-Ref 不一致。' }
    $Ref = $rawRef
} elseif ($env:URP_RAW -and -not $explicitRef) {
    throw '自定义 URP_RAW 必须同时明确设置 URP_REF/-Ref。'
}
if ($Ref -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or $Ref.Contains('..') -or $Ref.Contains('//') -or $Ref.EndsWith('/') -or $Ref.EndsWith('.')) { throw "无效的 ref：$Ref" }
$Raw = if ($env:URP_RAW) { $env:URP_RAW.TrimEnd('/') } elseif ($saved -and $saved.ref -ceq $Ref -and $saved.raw) { $saved.raw } else { "$RawRoot/$Ref" }
if (-not $Raw.StartsWith('https://') -or $Raw -match '[\r\n]') { throw 'URP_RAW 必须为单行 HTTPS URL。' }
$Tag = $env:URP_IMAGE_TAG
if (-not $Tag -and $saved -and $saved.ref -ceq $Ref) { $Tag = $saved.tag }
if (-not $Tag -and ($Ref -eq 'main' -or $Ref -match '^v[A-Za-z0-9_.-]*$' -or $Ref -cmatch '^[0-9a-f]{40}$')) { $Tag = $Ref }
if ($Tag -and $Tag -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$') { throw "无效的镜像标签：$Tag" }
$Components = if ($env:URP_COMPONENTS) { $env:URP_COMPONENTS } elseif ($saved -and $saved.components) { $saved.components } else { 'full' }
if ($Components -notin @('workbench', 'research', 'full')) { throw 'URP_COMPONENTS 只能是 workbench、research 或 full。' }
$Port = if ($env:URP_PORT) { $env:URP_PORT } elseif ($saved -and $saved.port) { $saved.port } else { '4100' }
if ($Port -notmatch '^[0-9]{1,5}$' -or [int]$Port -lt 1 -or [int]$Port -gt 65535) { throw 'URP_PORT 必须为 1–65535。' }
$Image = "ghcr.io/peakpeng666/urp-sim:$Tag"
$Health = "http://127.0.0.1:$Port/lab/api/health"
$Page = "http://localhost:$Port/"
$AssetRaw = $Raw

function Save-Selection {
    New-Item -ItemType Directory -Force -Path $State | Out-Null
    $selection = @{ ref = $Ref; raw = $Raw; tag = $Tag; components = $Components; port = $Port }
    $pending = Join-Path $State ('selection-' + [Guid]::NewGuid().ToString() + '.tmp')
    $selection | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $pending
    Move-Item -Force -LiteralPath $pending -Destination $selectionPath
}

function ConvertTo-BashLiteral {
    param([string]$Value)
    # POSIX single-quote escaping; refs, URLs, and environment values are data.
    return "'" + $Value.Replace("'", "'" + [char]34 + "'" + [char]34 + "'") + "'"
}

function Show-Visibility {
    Write-Host @'
匿名拉取失败，请检查网络、镜像标签及包的公开状态。
镜像发布者可检查：
  https://github.com/users/peakpeng666/packages/container/urp-sim/settings
  https://github.com/users/peakpeng666/packages/container/urp-bench/settings
'@
}

function Test-DockerEngine {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    try {
        docker info *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-ComposeFile {
    $deployDir = Join-Path $State 'deploy'
    New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
    $stage = Join-Path $deployDir ('download-' + [Guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $stage | Out-Null
    $names = @('docker-compose.sim.yml', 'docker-compose.gpu.yml', 'docker-compose.fuel.yml', 'docker-compose.display.yml')
    try {
        foreach ($name in $names) {
            Invoke-WebRequest -UseBasicParsing -Uri "$AssetRaw/deploy/$name" -OutFile (Join-Path $stage $name)
        }
        # Keep the previous files usable if any download fails part way through.
        foreach ($name in $names) {
            Move-Item -Force -LiteralPath (Join-Path $stage $name) -Destination (Join-Path $deployDir $name)
        }
    } finally {
        Remove-Item -Recurse -Force -LiteralPath $stage
    }
    return (Join-Path $deployDir 'docker-compose.sim.yml')
}

function Wait-Health {
    param([int]$Attempts = 90)
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Health -TimeoutSec 2
            if ($response.StatusCode -eq 200 -and $response.Content -match '"status":"ok"') { return $true }
        } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    $files = @('-f', (Join-Path $State 'deploy\docker-compose.sim.yml'))
    if ($env:URP_GPU -eq '1') { $files += @('-f', (Join-Path $State 'deploy\docker-compose.gpu.yml')) }
    if ($env:URP_DISPLAY -eq '1') { $files += @('-f', (Join-Path $State 'deploy\docker-compose.display.yml')) }
    $fuel = if ($env:URP_FUEL_DIR) { $env:URP_FUEL_DIR } else { Join-Path $State 'fuel' }
    if (Test-Path (Join-Path $fuel 'fuel.gazebosim.org')) {
        $env:URP_FUEL_DIR = $fuel
        $files += @('-f', (Join-Path $State 'deploy\docker-compose.fuel.yml'))
    }
    # Set only around Compose so another invocation in the same PowerShell
    # session does not mistake our old selection for a user's new override.
    $oldTag = $env:URP_IMAGE_TAG
    $oldPort = $env:URP_PORT
    try {
        $env:URP_IMAGE_TAG = $Tag
        $env:URP_PORT = $Port
        & docker compose @files @ComposeArgs
        if ($LASTEXITCODE -ne 0) { throw "docker compose 失败：$LASTEXITCODE" }
    } finally {
        $env:URP_IMAGE_TAG = $oldTag
        $env:URP_PORT = $oldPort
    }
}

function Invoke-DockerPath {
    param([string]$Action)
    if ($Action -eq 'down') {
        Invoke-Compose -ComposeArgs @('stop')
        Write-Host "容器已停止。数据卷还在。"
        return
    }
    if ($Action -eq 'status') {
        Invoke-Compose -ComposeArgs @('ps')
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Health
        Write-Host $response.Content
        return
    }
    if ($Action -eq 'up' -or $Action -eq 'update' -or $Action -eq 'fuel') {
        if (-not $Tag) { throw "ref=$Ref 没有默认发布镜像。请设置已发布的 v* 版本/完整提交号，或明确匹配源码的 URP_IMAGE_TAG；不会回落到其他通道。" }
        $platform = (& docker info --format '{{.OSType}}/{{.Architecture}}' | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $platform -notin @('linux/x86_64', 'linux/amd64')) { throw "需要 Linux x86_64 Docker Engine，当前：$platform" }
        Write-Host "安装来源：$Ref；镜像：$Image"
        docker pull $Image
        if ($LASTEXITCODE -ne 0) {
            docker image inspect $Image *> $null
            if ($LASTEXITCODE -ne 0) {
                Show-Visibility
                throw '镜像不可用。'
            }
            Write-Host "注册表暂时拉不到，使用本机已有的 $Image。"
        }
        $revision = (& docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' $Image | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $revision -cnotmatch '^[0-9a-f]{40}$') { throw '镜像缺少有效源码 revision 标签；拒绝混用未知版本文件。' }
        if ($Ref -cmatch '^[0-9a-f]{40}$' -and $revision -cne $Ref) { throw "镜像 revision=$revision 与 ref=$Ref 不一致。" }
        if ($Tag -cmatch '^[0-9a-f]{40}$' -and $revision -cne $Tag) { throw "镜像 revision=$revision 与镜像提交标签=$Tag 不一致。" }
        if ($Raw -ceq "$RawRoot/$Ref") { $script:AssetRaw = "$RawRoot/$revision" }
        Write-Host "镜像源码：$revision；部署文件：$AssetRaw"
        Get-ComposeFile | Out-Null
        if ($Action -eq 'fuel') {
            $dest = if ($env:URP_FUEL_DIR) { $env:URP_FUEL_DIR } else { Join-Path $State 'fuel' }
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            $dest = (Resolve-Path -LiteralPath $dest).Path
            $deployDir = (Resolve-Path -LiteralPath (Join-Path $State 'deploy')).Path
            $script = Join-Path $deployDir 'fetch-fuel.sh'
            Invoke-WebRequest -UseBasicParsing -Uri "$AssetRaw/deploy/fetch-fuel.sh" -OutFile $script
            # Docker Desktop understands Windows bind sources. A random bash.exe
            # might be WSL or Git Bash and cannot reliably consume C:\ paths.
            $downloadArgs = @('run', '--rm', '--pull', 'never', '--entrypoint', 'bash',
                '--mount', "type=bind,source=$deployDir,target=/installer,readonly",
                '--mount', "type=bind,source=$dest,target=/fuel")
            if ($env:URP_FUEL_ONLY) { $downloadArgs += @('-e', "URP_FUEL_ONLY=$($env:URP_FUEL_ONLY)") }
            $downloadArgs += @($Image, '/installer/fetch-fuel.sh', '/fuel')
            & docker @downloadArgs
            if ($LASTEXITCODE -ne 0) { throw "Fuel 下载失败，退出码：$LASTEXITCODE" }
            $env:URP_FUEL_DIR = $dest
        }
        Invoke-Compose -ComposeArgs @('up', '-d', '--no-build', '--pull', 'never', '--remove-orphans')
        if (Wait-Health) { Start-Process $Page }
        else { throw '容器已启动，但健康检查失败。请看 docker compose logs。' }
        Save-Selection
        Write-Host $Page
        return
    }
}

function Test-WslUbuntu {
    # Distribution name is not proof of WSL version or the installed OS.
    $listing = ((& wsl.exe --list --verbose | Out-String) -replace "`0", '')
    if ($LASTEXITCODE -ne 0) { throw '无法读取 WSL 发行版列表，请先修复 WSL。' }
    $match = [regex]::Match($listing, '(?m)^\s*\*?\s*Ubuntu-22\.04\s+\S+\s+([12])\s*$')
    if (-not $match.Success) { return $false }
    if ($match.Groups[1].Value -ne '2') {
        throw 'Ubuntu-22.04 当前是 WSL1。请先备份，再执行 wsl --set-version Ubuntu-22.04 2；安装器不会自动转换。'
    }
    $probe = @'
set -eu
. /etc/os-release
printf '%s|%s|%s|%s\n' "$ID" "$VERSION_ID" "$(uname -m)" "$(id -u)"
'@
    $identity = ((& wsl.exe -d Ubuntu-22.04 -e bash -lc $probe | Out-String) -replace "`0", '').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu-22.04 还不能执行命令。请先打开它，完成 Linux 用户创建后重试。' }
    $fields = $identity.Split('|')
    if ($fields.Count -ne 4 -or ($fields[0..2] -join '|') -ne 'ubuntu|22.04|x86_64') { throw "需要 WSL2 Ubuntu 22.04 x86_64；实际：$identity" }
    if ($fields[3] -eq '0') { throw '请先为 Ubuntu-22.04 配置普通 Linux 用户，再运行安装器。系统依赖安装时会提示输入 sudo 密码。' }
    return $true
}

function Invoke-WslPath {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw '没有 Docker，也没有 wsl.exe。请先安装 WSL2 Ubuntu 22.04：https://learn.microsoft.com/windows/wsl/install'
    }
    if (-not (Test-WslUbuntu)) {
        Write-Host '没有 Ubuntu-22.04。正在安装；可能需要管理员权限或重启。完成首次 Linux 用户创建后，重新运行安装器。'
        & wsl.exe --install -d Ubuntu-22.04 --no-launch
        if ($LASTEXITCODE -ne 0) { throw "命令失败，退出码：$LASTEXITCODE" }
        throw '请打开 Ubuntu-22.04 完成初始化，然后重试。安装器将在运行前核对 WSL2、Ubuntu22.04 和 x86_64。'
    }
    $environment = @{
        URP_REF = $Ref; URP_RAW = $Raw; URP_IMAGE_TAG = $Tag
        URP_COMPONENTS = $Components; URP_PORT = $Port
    }
    # Windows URP_HOME/FUEL_DIR paths are not Linux paths and are not forwarded.
    foreach ($name in @('URP_GPU', 'URP_DISPLAY')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $environment[$name] = $value }
    }
    $exports = ($environment.GetEnumerator() | ForEach-Object { 'export ' + $_.Key + '=' + (ConvertTo-BashLiteral $_.Value) }) -join "`n"
    $bootstrap = @'
set -euo pipefail
if ! command -v curl >/dev/null 2>&1; then
  echo '需要安装基础包 curl、ca-certificates 和 git。'
  if [[ "$EUID" == 0 ]]; then
    apt-get update
    apt-get install -y curl ca-certificates git
  elif [[ -t 0 || -t 2 ]] && command -v sudo >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y curl ca-certificates git
  else
    echo '请在 Ubuntu-22.04 终端执行 sudo apt-get update && sudo apt-get install -y curl ca-certificates git，然后重试。' >&2
    exit 127
  fi
fi
script="$(mktemp)"
trap 'rm -f "$script"' EXIT
curl -fsSL "$URP_RAW/install.sh" -o "$script"
bash "$script" __ACTION__
'@
    $bootstrap = $bootstrap.Replace('__ACTION__', (ConvertTo-BashLiteral $Command))
    # The Linux workbench stays in the foreground; remember the selection
    # before launch so closing that terminal does not reset the next invocation.
    Save-Selection
    & wsl.exe -d Ubuntu-22.04 -e bash -lc ($exports + "`n" + $bootstrap)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "WSL 安装器退出码：$code" }
}

if ($Ref -in @('deploy', 'redo') -and $Command -in @('up', 'update', 'fuel')) {
    throw '旧分支安装选择需要迁移：请重新取得 main/install.ps1，指定 -Ref main -Command update，或选择已验证发布的完整提交号；原数据与选择保留。'
}
if (Test-DockerEngine) {
    Invoke-DockerPath $Command
} else {
    Invoke-WslPath
}
