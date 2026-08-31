# One-time AWS setup for the S3 sync

`.github/workflows/sync-s3.yml` keeps `s3://ietf-meeting-vcons` in step with
`main`. It authenticates with OIDC rather than a stored access key: the job
exchanges a GitHub-signed token for a short-lived role session, so there is no
secret in this repository to leak or rotate.

The account already has the GitHub OIDC provider registered, so only the role
is missing. Run these once, with credentials for the account that owns the
bucket. Replace `ACCOUNT_ID` in `trust-policy.json` first.

```bash
aws iam create-role \
  --role-name github-actions-ietf-meeting-vcons-s3 \
  --assume-role-policy-document file://.github/aws/trust-policy.json \
  --description "Sync the published vCons to s3://ietf-meeting-vcons on push to main"

aws iam put-role-policy \
  --role-name github-actions-ietf-meeting-vcons-s3 \
  --policy-name s3-sync \
  --policy-document file://.github/aws/s3-sync-policy.json
```

Then point the workflow at the role. The ARN is a repository variable rather
than a literal in the workflow, to keep the account number out of a public
repo:

```bash
gh variable set AWS_ROLE_ARN \
  --repo vcon-dev/ietf-meeting-vcons \
  --body "arn:aws:iam::ACCOUNT_ID:role/github-actions-ietf-meeting-vcons-s3"
```

Confirm it works without waiting for a corpus change:

```bash
gh workflow run sync-s3.yml --repo vcon-dev/ietf-meeting-vcons
gh run watch --repo vcon-dev/ietf-meeting-vcons
```

## What the role can do

`s3:PutObject`, `s3:DeleteObject`, `s3:GetObject` on `ietf-meeting-vcons/*` and
`s3:ListBucket` on the bucket. Nothing else, and nothing outside that bucket.

The trust policy pins `sub` to
`repo:vcon-dev/ietf-meeting-vcons:ref:refs/heads/main`, so a workflow on a
branch, a tag, a pull request, or another repository cannot assume it.
