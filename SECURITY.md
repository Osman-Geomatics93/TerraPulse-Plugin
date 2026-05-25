# Security Policy

## Supported Versions

Only the current minor release series receives security patches.
Earlier releases are end-of-life and will not receive fixes.

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | ✅ Yes             |
| < 0.2   | ❌ No              |

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

Public disclosure before a patch is available can put users at risk.
Instead, use one of the private channels below:

### Option 1 — GitHub Private Security Advisory (preferred)

1. Go to the repository on GitHub.
2. Navigate to **Settings → Security → Advisories**.
3. Click **New draft security advisory**.
4. Fill in the template and submit.

GitHub will notify the maintainer privately and the report will remain
confidential until a coordinated disclosure is made.

### Option 2 — Email

Send a report directly to the maintainer:

**mohamed.fawzy98@hotmail.com**

Include as much detail as possible:
- A clear description of the vulnerability.
- Steps to reproduce or a proof-of-concept.
- Potential impact and affected versions.
- Any suggested mitigations you have already identified.

### Response Timeline

| Milestone                         | Target               |
| --------------------------------- | -------------------- |
| Acknowledgement of your report    | Within **48 hours**  |
| Confirmation of validity          | Within **7 days**    |
| Patch released (critical/high)    | Within **14 days**   |
| Patch released (medium/low)       | Within **30 days**   |
| Public disclosure (coordinated)   | After patch is live  |

We will credit you in the release notes unless you prefer to remain
anonymous.

---

## What We Consider a Security Issue

The following classes of vulnerability are in scope:

- **Hardcoded credentials** — API keys, passwords, or tokens committed
  to the repository or bundled in the plugin.
- **Code injection via plugin inputs** — any user-supplied string that
  reaches `subprocess`, `eval`, `exec`, or a shell command without
  sanitisation.
- **Insecure Docker socket exposure** — plugin logic that mounts or
  exposes `/var/run/docker.sock` in a way that allows container escape
  or privilege escalation.
- **Path traversal** — file-path inputs that allow reading or writing
  files outside the expected working directory.
- **Insecure deserialization** — loading untrusted pickle, YAML
  (with `yaml.load`), or similar formats that can execute arbitrary code.
- **Dependency with a known CVE** — a third-party package pinned to a
  version that has a published critical or high CVE.
- **Authentication bypass** — any logic that allows unauthorised access
  to protected functionality.

---

## What We Do NOT Consider a Security Issue

The following items are **out of scope** for this policy:

- **Flake8 / Ruff style warnings** — code-quality findings that do not
  represent an exploitable vulnerability.
- **detect-secrets false positives on key-name strings** — strings such
  as `"api_key"` used as dictionary keys or variable names, where no
  actual secret value is present.
- **Vulnerabilities in QGIS itself** — please report those to the QGIS
  project security team.
- **Vulnerabilities in third-party Docker base images** — upstream image
  maintainers are responsible for their own security.
- **Denial-of-service through large SAR datasets** — processing very
  large datasets is an expected use case and is not treated as a
  security vulnerability.

---

## Security Tools Used in CI

Every pull request and main-branch push runs the following security
scanners automatically:

| Tool             | Purpose                                              |
| ---------------- | ---------------------------------------------------- |
| **Bandit**       | Static analysis for common Python security issues    |
| **detect-secrets** | Scans for accidentally committed secrets/credentials |

Findings from these tools that represent genuine vulnerabilities will
be addressed before merging.
