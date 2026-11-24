# AR-forge-missing-report: Stats without a dynamic-access report

How the lane `finalize` and `audit` share (§AR-forge-workflow-pipeline.4) keeps
a failed report build away from the coverage gate
(§FS-local-ci-equivalent-verification.1.1).

`generateLibraryStats` runs `generateDynamicAccessReport` itself, and passes
when that build fails, recording `dynamicAccess` as `N/A`. The harness task
keeps doing so, because the code coverage workflow relies on it
(§AR-code-coverage-improvement.5); only Forge's lane, which judges the figure,
refuses it. After the task the lane reads the version's stats entry, and an
entry without a report fails the step as `generateDynamicAccessReport`, the
command that fails for an agent too, with the task's output, which streams that
build's. A library without dynamic access and a JAR without bytecode have a
report with zero calls, so neither is one of these.

The output is read as any failed build's: a repository that could not be reached
is an infrastructure failure (§AR-forge-workflow-pipeline.4.1), a stale daemon's
failure reruns the step, and the rest is a fix request. The Native Image
driver's own line that its build ran out of memory, which it prints for a kill
by the kernel too, stops the run instead (§FS-human-intervention-policy): the
program exits `12` with no fix request, whatever is left of its budget
(§AR-forge-workflow-pipeline.6), and the outcome's reason is the build and that
cause.

The gate command, which an agent reruns, refuses a version without a report too.
