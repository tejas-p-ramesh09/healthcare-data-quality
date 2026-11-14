# Project instructions

- Work on one milestone at a time. Present the plan and acceptance criteria and obtain the project owner's approval before implementation.
- Explain significant design choices, alternatives, and tradeoffs so the owner can understand and defend the work.
- For each implementation milestone, create a GitHub Issue, work on a focused feature branch, make meaningful incremental commits, open a pull request linked to the issue, and require passing CI checks before merge. Obtain explicit approval before pushing, merging, deploying, or making destructive changes. Leave the initial commit pending until the owner reviews the setup.
- Use only synthetic patient data in datasets, tests, demos, and reports. Record generator versions, configurations, seeds, and experiment inputs.
- Test important behavior, including failures and edge cases. Report only observed results and identify unverified claims as proposals or targets.
- Favor simple components. Introduce additional services or infrastructure only when a requirement justifies the cost.
- Close each task with changes, checks and results, limitations, and the next task.

Read the [project context](docs/PROJECT_CONTEXT.md), [decision log](docs/DECISIONS.md), and [progress tracker](docs/PROGRESS.md) before milestone work.

## Milestone workflow

1. Present the implementation plan, acceptance criteria, tests, and intended GitHub repository and branch. List the actions the owner is approving. Approval authorizes only the listed actions for that milestone: creating or reusing an issue, creating a feature branch, making incremental commits, pushing that branch, creating or updating its PR, and verifying CI. An explicitly listed feature-branch push satisfies the push approval rule above for that milestone.
2. Before remote operations, verify that the standalone Git root is `/Users/tejaspramesh/Documents/Projects/healthcare-data-quality`, the authenticated GitHub CLI account is `tejas-p-ramesh09`, both fetch and push URLs for `origin` are `https://github.com/tejas-p-ramesh09/healthcare-data-quality.git`, and the current branch matches the approved step. Stop and report any identity or authorization mismatch.
3. Use GitHub CLI to look for the milestone's issue, branch, and PR before creating anything. Reuse matching records. Start new work on a focused `codex/<milestone>` branch from the verified base.
4. Implement in small increments. Run relevant tests and inspect the diff before committing. Update progress and technical documentation with verified results, reproduction steps, and limitations. Each commit must contain a meaningful change.
5. Push the approved feature branch, verify its remote tip, and create or update its PR into `main` with `Closes #<issue-number>`. Watch GitHub Actions and confirm checks apply to the current PR head. Keep PR validation permissions read-only unless the approved milestone explicitly requires more.
6. If CI fails, read the job logs and report the cause. Fix issues within the approved scope, rerun local checks, and verify the replacement CI run. Seek approval for scope changes. Never weaken tests, bypass CI, fabricate results, change acceptance criteria to get a passing check, or create artificial commits. Report any failing, pending, or unavailable checks.
7. Report the issue and PR URLs, commits, verification results, limitations, and merge status. Obtain separate explicit approval before merging, deploying, deleting branches, rewriting history, making destructive changes, or changing repository protection settings.
