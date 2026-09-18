[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$natName = 'G1WifiNAT'
$ethernetIndex = 13

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script as Administrator.'
}

$nat = Get-NetNat -Name $natName -ErrorAction SilentlyContinue
if ($nat) {
    Remove-NetNat -Name $natName -Confirm:$false
    Write-Host "Removed $natName."
}

Set-NetIPInterface -InterfaceIndex $ethernetIndex -AddressFamily IPv4 -Forwarding Disabled
Write-Host 'IPv4 forwarding is disabled on Ethernet interface 13.'
Write-Host 'To restore the G1 original gateway, run this command on the G1:'
Write-Host 'sudo ip route replace default via 192.168.123.1 dev eth0'
Read-Host 'Press Enter to close'
