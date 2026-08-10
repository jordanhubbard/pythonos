# Python implementation

### Requirement: Portable Python runtime

The generated application SHALL use Python 3.11 or newer found as `python3` or `python`
on PATH and SHALL require no third-party packages.

#### Scenario: Host discovery

- **WHEN** no exact Python executable is pinned
- **THEN** generation uses the first compatible `python3` or `python` on PATH
