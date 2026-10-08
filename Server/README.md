## Running tests

From PowerShell, run the server test suite with:

```powershell
.\run_tests.ps1
```

The script runs every `test*.py` file under `tests` in sequence and writes
`result.html` in the `Server` directory. Open the report automatically with
`.\run_tests.ps1 -OpenReport`, or choose a different report path with
`.\run_tests.ps1 -ReportPath .\reports\result.html`.