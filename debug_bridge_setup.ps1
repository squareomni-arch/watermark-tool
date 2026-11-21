# Cài cầu nối SSH để Mac debug máy Windows qua cùng mạng LAN.
# Chạy bằng quyền Administrator:
#   powershell -NoProfile -ExecutionPolicy Bypass -File debug_bridge_setup.ps1 -AppDir "C:\path\to\watermark-tool"
param(
    [string]$AppDir = ""
)

$ErrorActionPreference = "Stop"

$PublicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEnZzecGmdRoOrkVj3INKnAEzJH/nzXGgucQpSUiaMND watermark-debug@mac"

Write-Host "=== CÀI OPENSSH SERVER ==="

# Cài OpenSSH Server nếu chưa có.
try {
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue | Out-Null
} catch {
    # Windows cũ hơn không hỗ trợ Add-WindowsCapability.
}

Start-Service sshd -ErrorAction SilentlyContinue
Set-Service -Name sshd -StartupType Automatic

# Mở firewall cho port 22.
try {
    if (-not (Get-NetFirewallRule -DisplayName "OpenSSH Server (sshd)" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name "OpenSSH-Server-sshd" -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    }
} catch {
    netsh advfirewall firewall add rule name="OpenSSH Server (sshd)" dir=in action=allow protocol=TCP localport=22 | Out-Null
}

Write-Host "=== CÀI PUBLIC KEY CHO USER HIỆN TẠI ==="

$current = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($current)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    $sshDir = Join-Path $env:ProgramData "ssh"
    $authFile = Join-Path $sshDir "administrators_authorized_keys"
} else {
    $sshDir = Join-Path $env:USERPROFILE ".ssh"
    $authFile = Join-Path $sshDir "authorized_keys"
}

New-Item -ItemType Directory -Path $sshDir -Force | Out-Null

$existing = @()
if (Test-Path $authFile) {
    $existing = @(Get-Content $authFile)
}
if ($PublicKey -notin $existing) {
    Add-Content -Path $authFile -Value $PublicKey
}

# Ghi thông tin để Mac đọc sau khi SSH vào.
$info = @"
COMPUTERNAME=$env:COMPUTERNAME
USERNAME=$env:USERNAME
APPDIR=$AppDir
IP=$((Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback' -and $_.IPAddress -notmatch '^169\.254\.' } | Select-Object -First 1 -ExpandProperty IPAddress))
"@

$infoPath = Join-Path $env:PUBLIC "watermark_debug_info.txt"
$info | Set-Content -Path $infoPath -Encoding Ascii

Write-Host ""
Write-Host "=== XONG ==="
Write-Host $info
Write-Host "Info file: $infoPath"
Write-Host ""
Write-Host "Bây giờ trên Mac có thể chạy:"
Write-Host "  ssh -i ~/.ssh/watermark_debug $env:USERNAME@<IP>"
Write-Host ""
