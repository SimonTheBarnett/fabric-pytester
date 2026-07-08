# upload-testpypi.ps1

$ErrorActionPreference = "Stop"

$Token = Read-Host "Paste TestPyPI API token"

if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "TestPyPI API token is required."
}

if (-not $Token.StartsWith("pypi-")) {
    throw "The token should start with 'pypi-'."
}

try {
    Write-Host "Uploading to TestPyPI..."

    uv run twine upload `
        --repository-url https://test.pypi.org/legacy/ `
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
