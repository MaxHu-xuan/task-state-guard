# Support

TaskStateGuard is an alpha-stage open-source library maintained on a best-effort
basis. No response time, availability, data recovery, or compatibility service
level is promised.

## Where to ask

- Use a GitHub bug report for a reproducible defect involving synthetic data.
- Use a GitHub feature request for a proposed public API or documented behavior.
- Use a pull request for a focused, tested change that follows
  `CONTRIBUTING.md`.
- Report security vulnerabilities through GitHub private vulnerability
  reporting as described in `SECURITY.md`.

Do not post a production database, task identifier, payload fingerprint,
filesystem path, credential, private task body, or other confidential material.
Maintainers may close or redact reports that cannot be handled safely in public.

## Supported scope

Useful reports cover the current default branch or the latest published release
on CPython 3.11 through 3.14, using a supported local filesystem on Linux, macOS,
or Windows. Include the operating system, Python version, package version, a
minimal synthetic reproduction, and expected versus observed state transitions.

The project cannot provide operational support for Windows DACL design, host
hardening, disk encryption, SQLite repair, remote or mapped filesystems,
distributed consensus, scheduling, task execution, transport delivery, or
recovery of external side effects. Those remain responsibilities of the
embedding runtime and its operator.
