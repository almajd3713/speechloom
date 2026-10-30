# Security policy

## Supported versions

Security fixes go into the latest release on PyPI.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub: open the repository's **Security** tab
and choose **Report a vulnerability**. Please do not open a public issue for a security
problem.

## Scope

Speechloom runs locally. The most security-relevant parts are:

- the optional HTTP API (`speechloom serve`), which must reject requests from other
  websites and must not expose files outside its job directories and allowed roots;
- `speechloom setup`, which downloads engines and models and must verify their
  checksums;
- the handling of media files and file names supplied by users or API clients.
