$ErrorActionPreference = "Stop"

uv version --bump patch

Write-Host "Version bumped:"
uv version