# Security policy

## Supported versions

Security fixes are handled on `main` until the project starts publishing
versioned maintenance branches.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities that expose credentials, command
injection, unsafe file writes, or unintended network upload behavior.

Report security issues privately through GitHub Security Advisories for the
repository, or contact the maintainers through the private channel listed on the
project profile.

Include:

- affected version or commit;
- operating system and Node/Python versions;
- exact command, config, or report artifact involved;
- whether credentials, environment variables, or source files were exposed;
- a minimal reproducer when safe to share.

## Scope

Security-sensitive areas include:

- build, check, and test command execution;
- environment-variable injection and redaction;
- dashboard upload authentication;
- retained worker directories and artifact cleanup;
- source mutation and restoration;
- package release provenance.

Provider orchestration security in Marmorkrebs should be reported against the
Marmorkrebs repository unless the bug is in `stryker-cxx` itself.
