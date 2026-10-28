# Shared Windows CurrentUser DPAPI helpers.  These functions never write a
# plaintext secret and intentionally return only an in-memory value to the
# caller that is about to start the API or Worker child process.

try { $null = [Security.Cryptography.ProtectedData] } catch { try { Add-Type -AssemblyName System.Security -ErrorAction Stop } catch { throw 'Windows CurrentUser DPAPI is unavailable' } }

function Get-DpapiFailure([string]$Operation, [Exception]$Exception) {
    $hresult = '{0:X8}' -f ([int64]$Exception.HResult -band 0xffffffff)
    $innerType = if ($null -ne $Exception.InnerException) { $Exception.InnerException.GetType().FullName } else { 'none' }
    return "DPAPI_${Operation}_FAILED exception_type=$($Exception.GetType().FullName) inner_type=$innerType hresult=0x$hresult category=current-user-profile-or-identity"
}

function Remove-DpapiProcessApiKey {
    Remove-Item -LiteralPath 'Env:API_KEY' -ErrorAction SilentlyContinue
    [Environment]::SetEnvironmentVariable('API_KEY', $null, 'Process')
    Remove-Item -LiteralPath 'Env:API_KEY' -ErrorAction SilentlyContinue
}

function Get-DpapiCurrentUserSid {
    try {
        return ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    } catch {
        throw 'Unable to identify the current Windows user for protected secret access'
    }
}

function Get-DpapiIdentitySid([object]$IdentityReference) {
    try {
        return $IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        return $null
    }
}

function Assert-DpapiNoReparse([string]$Path, [string]$Name) {
    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    } catch {
        $originalMetadataError = $_
        throw [Exception]::new('Protected runtime metadata check failed at stage=GetItem', $originalMetadataError.Exception)
    }
    if ($null -eq $item -or $item -isnot [IO.FileSystemInfo]) {
        throw 'Protected runtime metadata result invalid or empty at stage=MetadataResult'
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Name must not be a symbolic link or reparse point"
    }
    return $item
}

function Get-DpapiAcl([string]$Path, [string]$Name) {
    try {
        return Get-Acl -LiteralPath $Path -ErrorAction Stop
    } catch {
        throw "$Name ACL is inaccessible"
    }
}

function Set-DpapiProtectedAcl([string]$Path, [bool]$Directory, [string]$Name) {
    $item = Assert-DpapiNoReparse $Path $Name
    try {
        $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
        $expected = @('S-1-5-18', (Get-DpapiCurrentUserSid))
        $currentSids = @($acl.Access | ForEach-Object { Get-DpapiIdentitySid $_.IdentityReference })
        if ($acl.AreAccessRulesProtected -and @($acl.Access).Count -eq 2 -and @($currentSids | Where-Object { $_ -notin $expected }).Count -eq 0 -and @($expected | Where-Object { $_ -notin $currentSids }).Count -eq 0 -and @($acl.Access | Where-Object { $_.IsInherited -or $_.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow }).Count -eq 0) {
            Assert-DpapiProtectedAcl $Path $Directory $Name
            return
        }
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($entry in @($acl.Access)) {
            $sid = Get-DpapiIdentitySid $entry.IdentityReference
            if ($null -ne $sid) { $null = $acl.RemoveAccessRuleSpecific($entry) }
        }
        $systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
        $userSid = New-Object Security.Principal.SecurityIdentifier((Get-DpapiCurrentUserSid))
        $rights = [Security.AccessControl.FileSystemRights]::FullControl
        $allow = [Security.AccessControl.AccessControlType]::Allow
        if ($Directory) {
            $inherit = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
            $ruleSystem = New-Object Security.AccessControl.FileSystemAccessRule($systemSid, $rights, $inherit, [Security.AccessControl.PropagationFlags]::None, $allow)
            $ruleUser = New-Object Security.AccessControl.FileSystemAccessRule($userSid, $rights, $inherit, [Security.AccessControl.PropagationFlags]::None, $allow)
        } else {
            $ruleSystem = New-Object Security.AccessControl.FileSystemAccessRule($systemSid, $rights, $allow)
            $ruleUser = New-Object Security.AccessControl.FileSystemAccessRule($userSid, $rights, $allow)
        }
        $acl.AddAccessRule($ruleSystem)
        $acl.AddAccessRule($ruleUser)
        Set-Acl -LiteralPath $Path -AclObject $acl -ErrorAction Stop
    } catch {
        throw "$Name ACL could not be hardened"
    }
    Assert-DpapiProtectedAcl $Path $Directory $Name
}

function Assert-DpapiProtectedAcl([string]$Path, [bool]$Directory, [string]$Name) {
    Assert-DpapiNoReparse $Path $Name | Out-Null
    $acl = Get-DpapiAcl $Path $Name
    $expected = @('S-1-5-18', (Get-DpapiCurrentUserSid))
    $seen = @{}
    foreach ($entry in @($acl.Access)) {
        if ($entry.IsInherited -or $entry.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            throw "$Name ACL is not restricted to explicit allow rules"
        }
        $sid = Get-DpapiIdentitySid $entry.IdentityReference
        if ($null -eq $sid -or $sid -notin $expected) { throw "$Name ACL contains an unexpected identity" }
        if (([Security.AccessControl.FileSystemRights]$entry.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl) {
            throw "$Name ACL does not grant the required protected access"
        }
        $seen[$sid] = $true
    }
    if ($seen.Count -ne 2 -or @($expected | Where-Object { !$seen.ContainsKey($_) }).Count -ne 0) {
        throw "$Name ACL must contain only the current user and SYSTEM"
    }
}

function Assert-DpapiUnderRoot([string]$Path, [string]$Root, [string]$Name) {
    try { $full = [IO.Path]::GetFullPath($Path); $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\' } catch { throw "$Name path is invalid" }
    if (!$full.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) { throw "$Name must remain inside the protected runtime directory" }
    if ($full -eq $rootFull.TrimEnd('\')) { throw "$Name must be a child of the protected runtime directory" }
    return $full
}

function Assert-DpapiSecretFile([string]$Path, [string]$RuntimeRoot, [string]$Name = 'Protected provider secret') {
    $full = Assert-DpapiUnderRoot $Path $RuntimeRoot $Name
    $item = Assert-DpapiNoReparse $full $Name
    if ($item.PSIsContainer) { throw "$Name must be a regular file" }
    if ($item.Length -lt 1 -or $item.Length -gt 1048576) { throw "$Name has an invalid protected payload" }
    Assert-DpapiProtectedAcl $full $false $Name
    return $full
}

function Protect-DpapiSecretToFile([string]$Secret, [string]$Path, [string]$RuntimeRoot) {
    if ([string]::IsNullOrWhiteSpace($Secret) -or $Secret.Length -gt 4096) { throw 'Provider secret is empty or too long' }
    $full = Assert-DpapiUnderRoot $Path $RuntimeRoot 'Protected provider secret'
    $parent = [IO.Path]::GetDirectoryName($full)
    Assert-DpapiNoReparse $parent 'Protected runtime directory' | Out-Null
    if (Test-Path -LiteralPath $full) { Assert-DpapiNoReparse $full 'Protected provider secret' | Out-Null }
    $plain = [Text.Encoding]::UTF8.GetBytes($Secret)
    $cipher = $null
    $tmp = "$full.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
    $stage = 'protect'
    try {
        $cipher = [Security.Cryptography.ProtectedData]::Protect($plain, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $stage = 'write'
        [IO.File]::WriteAllBytes($tmp, $cipher)
        $stage = 'acl'
        Set-DpapiProtectedAcl $tmp $false 'Protected provider secret temporary file'
        $stage = 'move'
        Move-Item -LiteralPath $tmp -Destination $full -Force
        $stage = 'verify'
        Assert-DpapiProtectedAcl $full $false 'Protected provider secret'
    } catch {
        if ($stage -eq 'protect') { throw (Get-DpapiFailure 'PROTECT' $_.Exception) }
        throw "Provider secret could not be written to the protected runtime directory (stage=$stage)"
    } finally {
        if ($plain) { [Array]::Clear($plain, 0, $plain.Length) }
        if ($cipher) { [Array]::Clear($cipher, 0, $cipher.Length) }
        if (Test-Path -LiteralPath $tmp -PathType Leaf) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
    return $full
}

function Unprotect-DpapiSecretFromFile([string]$Path, [string]$RuntimeRoot) {
    $full = Assert-DpapiSecretFile $Path $RuntimeRoot
    $cipher = $null
    $plain = $null
    try {
        $cipher = [IO.File]::ReadAllBytes($full)
        $plain = [Security.Cryptography.ProtectedData]::Unprotect($cipher, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        if ($null -eq $plain -or $plain.Length -eq 0) { throw 'empty' }
        $secret = [Text.Encoding]::UTF8.GetString($plain)
        if ([string]::IsNullOrWhiteSpace($secret) -or $secret.IndexOf([char]0) -ge 0 -or $secret.Length -gt 4096) { throw 'invalid' }
        return $secret
    } catch {
        throw (Get-DpapiFailure 'UNPROTECT' $_.Exception)
    } finally {
        if ($cipher) { [Array]::Clear($cipher, 0, $cipher.Length) }
        if ($plain) { [Array]::Clear($plain, 0, $plain.Length) }
    }
}
