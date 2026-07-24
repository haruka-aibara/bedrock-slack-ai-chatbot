# CLAUDE.md

## CI チェック

PR / push で以下の CI が走る。コードを書いたら必ず通ることを確認してからコミットすること。

| チェック | コマンド | 備考 |
|----------|----------|------|
| Terraform fmt | `terraform fmt -recursive -check` | 差分があると exit 1 |
| tflint | `tflint -f compact` | warning でも exit 2 |
| Trivy | `trivy config .` | HIGH/CRITICAL で exit 1 |
| ruff | `uvx ruff check .` | Python Test 以外は再利用ワークフロー呼び出し |
| pytest | `uv run pytest` | `.github/workflows/python-test.yml` |

`.github/workflows/python-ci.yml` と `terraform-ci.yml` は別リポジトリ (haruka-aibara/haruka-aibara) の Terraform が生成しているので直接編集しない。このリポジトリ固有のチェックを足すときは `python-test.yml` のような別ファイルを追加する。

### Python コードを変更したら必ずやること

```bash
uv run pytest
uvx ruff check .
```

テストは `tests/` に置く。Lambda のソースディレクトリ (`lambda_function_*/`) は `data.archive_file` で zip 化されるので、テストファイルを混ぜない。混ぜると `source_code_hash` が変わって無関係な再デプロイが走る。

### Terraform ファイルを変更したら必ずやること

```bash
terraform fmt -recursive
```

fmt は自分で実行して修正済みの状態でコミットする。CI に任せない。

## IAM ポリシードキュメントの書き方

`data "aws_iam_policy_document"` 内でリソース ARN を参照するとき、`resource.name.arn` や `resource.name.function_name` のようなリソース参照を使わず、ARN を文字列で組み立てる。

```hcl
# Bad — リソース参照は plan 時に (known after apply) になりポリシー全体の diff が潰れる
resources = [aws_sqs_queue.foo.arn]

# Good — plan 時に確定する
resources = ["arn:aws:sqs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:${local.project_name}-queue"]
```
