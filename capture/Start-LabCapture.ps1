[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ManagementHost,
    [Parameter(Mandatory)] [string] $User,
    [Parameter(Mandatory)] [string] $Key,
    [Parameter(Mandatory)] [string] $KnownHosts,
    [ValidateSet('gadget','loopback','usbmon')] [string] $Layer = 'gadget',
    [ValidateRange(1,120)] [int] $Seconds = 30,
    [ValidateRange(1,32)] [int] $MiB = 8,
    [string] $Filter,
    [string] $Save,
    [switch] $PrivateUsb,
    [switch] $NoGui
)
$callArgs = @((Join-Path $PSScriptRoot 'remote_live.py'),'--host',$ManagementHost,'--user',$User,'--key',$Key,'--known-hosts',$KnownHosts,'--layer',$Layer,'--seconds',"$Seconds",'--mib',"$MiB")
if($Filter){$callArgs += @('--filter',$Filter)}
if($Save){$callArgs += @('--save',$Save)}
if($PrivateUsb){$callArgs += '--private-usb'}
if($NoGui){$callArgs += '--no-gui'}
& py -3 @callArgs
exit $LASTEXITCODE
