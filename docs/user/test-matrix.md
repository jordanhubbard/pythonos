# Private test matrix

`litai init` creates one synthetic `literate.test.example.json` to introduce optional
cross-platform fanout. Copy it to the ignored `literate.test.json`, then replace every
placeholder with a user-owned `username@hostname:target-directory` assignment and its
matching `linux`, `macos`, or `windows` Flavor.

Never commit `literate.test.json`. Worker names, credentials, dynamic provisioning
skills, and private routes are operator/session configuration rather than application
authority. Set `LITAI_TEST_CONFIG=/absolute/path/to/private-matrix.json` when the matrix
must live outside the project.

The framework's sample suite currently drives this schema with `make
samples-platform-regression TEST_CONFIG=/path/to/matrix.json SAMPLE='*'`. A derived
project may connect the same private matrix to its CI or task router and run `litai
rebuild` for the selected Component and platform Flavor on each worker. The packaged
CLI does not yet claim a generic derived-project fanout verb; consult `litai help` for
the commands actually supported by the installed release.
