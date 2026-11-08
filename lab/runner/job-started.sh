#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Pre-job hook of the lab's self-hosted runner: the runner calls it before
# every job it is given (ACTIONS_RUNNER_HOOK_JOB_STARTED in its .env file,
# docs/lab.md), outside the job's control, and a non-zero exit fails the job
# before any of its steps.
#
# The runner is the lab's build host, so it takes only jobs that a
# collaborator started: pushes to main, release tags and manual runs. Pull
# requests never run here, whatever a workflow file says, as a pull request
# from a fork can bring its own workflows (docs/adr/0005).

set -eu

event=${GITHUB_EVENT_NAME:-}
ref=${GITHUB_REF:-}

case "$event:$ref" in
push:refs/heads/main | push:refs/tags/v* | workflow_dispatch:*)
	exit 0
	;;
esac

echo "::error::this runner takes no '$event' job for '$ref' (lab/runner/job-started.sh, docs/adr/0005)"
exit 1
