# PIE command line (pipeline, knowledge building, exports), run from the project root.
#   .\pie.ps1 fields-build --enrich
#   .\pie.ps1 usage-build --module inventory
#   .\pie.ps1 chat --question "What features should I highlight for the pump industry?"
#   .\pie.ps1 --help
Set-Location $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot "backend"
& ".\.venv\Scripts\python.exe" -m pie_agent.cli @args
exit $LASTEXITCODE
