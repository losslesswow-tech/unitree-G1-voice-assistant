[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$natName = 'G1WifiNAT'
$internalPrefix = '192.168.123.0/24'
$gatewayAddress = '192.168.123.100'
$ethernetIndex = 13
$robotAddress = '192.168.123.164'

function Require-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script as Administrator.'
    }
}

Require-Administrator

$ethernetAddress = Get-NetIPAddress -InterfaceIndex $ethernetIndex -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -eq $gatewayAddress -and $_.PrefixLength -eq 24 }
if (-not $ethernetAddress) {
    throw "Ethernet interface $ethernetIndex does not have $gatewayAddress/24. Stopped without changes."
}

$existingNat = @(Get-NetNat -ErrorAction Stop)
if ($existingNat.Count -gt 0) {
    $sameNat = $existingNat | Where-Object {
        $_.Name -eq $natName -and $_.InternalIPInterfaceAddressPrefix -eq $internalPrefix
    }
    if (-not $sameNat) {
        throw "Existing Windows NAT found: $($existingNat.Name -join ', '). Stopped without changes."
    }
    Write-Host "Existing $natName found. Skipping NAT creation."
}
else {
    Set-NetIPInterface -InterfaceIndex $ethernetIndex -AddressFamily IPv4 -Forwarding Enabled
    New-NetNat -Name $natName -InternalIPInterfaceAddressPrefix $internalPrefix | Out-Null
    Write-Host "Created $natName for $internalPrefix."
}

Write-Host ''
Write-Host 'Windows NAT is ready. The next step changes the G1 gateway and DNS.'
Write-Host 'Enter the SSH and sudo passwords only in this local window. They are not stored.'
Read-Host 'Press Enter to connect to G1'

$remoteCommand = "sudo ip route replace default via $gatewayAddress dev eth0; sudo systemd-resolve --interface=eth0 --set-dns=1.1.1.1 --set-dns=8.8.8.8; ip route show; systemd-resolve --status eth0"
ssh -tt "unitree@$robotAddress" $remoteCommand

if ($LASTEXITCODE -ne 0) {
    throw "G1 configuration did not finish (ssh exit code: $LASTEXITCODE). Windows NAT remains configured."
}

Write-Host ''
Write-Host 'G1 network settings have been updated.'
Write-Host 'On the G1, verify Internet access with:'
Write-Host 'python3 -c "import urllib.request; print(urllib.request.urlopen(''https://www.unitree.com'', timeout=15).status)"'
Read-Host 'Press Enter to close'
