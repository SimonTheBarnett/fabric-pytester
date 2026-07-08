# upload-pypi.ps1

$ErrorActionPreference = "Stop"

$Token = Read-Host "Paste PyPI API token"

if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "PyPI API token is required."
}

if (-not $Token.StartsWith("pypi-")) {
    throw "The token should start with 'pypi-'."
}

try {
    Write-Host "Uploading to PyPI..."

    uv run twine upload `
        --username __token__ `
        --password $Token `
        dist/*

    if ($LASTEXITCODE -ne 0) {
        throw "Twine upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done."
}
finally {
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        $Token = $null
    }
}
