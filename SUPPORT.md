# Support

Where to get help with the Lattice Transfer Protocol (LTP).

## Documentation

- Start at the persona-routed docs landing page: [`docs/README.md`](docs/README.md)
- Protocol questions: [`docs/WHITEPAPER.md`](docs/WHITEPAPER.md) and
  [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md)
- Operating a deployment: [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md)
  and [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md)
- Contributing and local setup: [`CONTRIBUTING.md`](CONTRIBUTING.md) and
  [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)

## Questions and bug reports

- Open a [GitHub issue](https://github.com/Suwappu-Labs/suwappu-lattice-protocol/issues)
  using the issue templates. Include the LTP version, Python version, and a
  minimal reproduction where possible.
- Check the [changelog](CHANGELOG.md) and
  [stability promises](docs/STABILITY_PROMISES.md) before reporting behavior
  changes across versions.

## Security issues

Do **not** open a public issue for vulnerabilities. Follow the private
disclosure process in [`SECURITY.md`](SECURITY.md).

## Verifying your environment

```bash
scripts/verify.sh        # run every local verification lane
scripts/verify.sh fast   # fail-fast Python suite only
```
