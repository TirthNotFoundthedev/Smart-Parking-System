# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

This directory (`Server`, part of the Design Thinking Project) currently contains only `README.md`. There is no source code, build config, or test suite yet, and the git repo has no commits. Update this file once the server code lands.

## Tests

Per the README, tests are meant to be run from PowerShell (not WSL/zsh):

```powershell
.\run_tests.ps1                                   # runs every tests/test*.py in sequence, writes result.html
.\run_tests.ps1 -OpenReport                       # also opens the report
.\run_tests.ps1 -ReportPath .\reports\result.html # custom report path
```

`run_tests.ps1` and the `tests/` directory are described but do not exist yet. There is no documented way to run a single test; tests are plain `test*.py` files, so a single file can presumably be run directly with Python once they exist.
