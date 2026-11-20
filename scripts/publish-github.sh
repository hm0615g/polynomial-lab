#!/usr/bin/env bash
# Publish this dedicated checkout to the authenticated user's own GitHub account.
set -euo pipefail
export GH_HOST=github.com

repo_name="polynomial-lab"
publish_public=false
dry_run=false

usage() {
  cat <<'HELP'
Polynomial Lab を自分の GitHub アカウントの公開リポジトリへ公開します。

使い方:
  bash scripts/publish-github.sh --public [--name polynomial-lab]
  bash scripts/publish-github.sh --public --dry-run

事前準備:
  1. Git、Node.js、Python、GitHub CLI (gh) をインストール。
  2. gh auth login --hostname github.com --git-protocol https --scopes workflow
  3. python3 -m venv .venv
  4. .venv/bin/python -m pip install -r requirements.txt

--public はソースコードとアプリを公開する意思の指定です。
既存の無関係なリポジトリを上書きせず、force push もしません。
再実行時は、このチェックアウトの origin が一致する場合のみ続行します。
HELP
}

while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --public) publish_public=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    --name)
      if (($# < 2)); then printf '%s\n' '--name の後にリポジトリ名を指定してください。' >&2; exit 2; fi
      repo_name="$2"; shift 2 ;;
    *) printf '不明なオプション: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$publish_public" != true ]]; then
  printf '%s\n' '公開するには --public を明示してください。--help で手順を表示します。' >&2
  exit 2
fi
if [[ ! "$repo_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ ]] || [[ "$repo_name" == *.git ]]; then
  printf '%s\n' 'リポジトリ名が不正です。英数字で始まる100文字以内の名前を指定してください。' >&2
  exit 2
fi

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$project_dir"
for program in git gh node python3; do
  if ! command -v "$program" >/dev/null 2>&1; then
    printf '必要なコマンドがありません: %s\n' "$program" >&2
    exit 1
  fi
done
if ! gh auth status --hostname github.com >/dev/null 2>&1; then
  printf '%s\n' 'GitHub CLI が未ログインです。gh auth login --hostname github.com --git-protocol https --scopes workflow を実行してから再実行してください。' >&2
  exit 1
fi

account_login="$(gh api user --jq .login)"
account_id="$(gh api user --jq .id)"
if [[ ! "$account_login" =~ ^[A-Za-z0-9-]+$ ]] || [[ ! "$account_id" =~ ^[0-9]+$ ]]; then
  printf '%s\n' 'ログイン中の GitHub アカウントを確認できませんでした。' >&2
  exit 1
fi
target_repo="${account_login}/${repo_name}"
target_url="https://github.com/${target_repo}.git"

validate_origin_push_urls() {
  local resolved_push_urls push_url
  if ! resolved_push_urls="$(git remote get-url --push --all origin)" || [[ -z "$resolved_push_urls" ]]; then
    printf '%s\n' 'origin の実際の push 先を確認できないため中止しました。' >&2
    return 1
  fi
  while IFS= read -r push_url; do
    if [[ "$push_url" != "$target_url" && "$push_url" != "git@github.com:${target_repo}.git" ]]; then
      printf '%s\n' 'origin の実際の push 先が指定先と異なるため中止しました。Git の pushurl / pushInsteadOf 設定を確認してください。' >&2
      return 1
    fi
  done <<< "$resolved_push_urls"
}

existing_origin=""
if [[ -e .git ]]; then
  existing_origin="$(git remote get-url origin 2>/dev/null || true)"
  if [[ -n "$existing_origin" && "$existing_origin" != "$target_url" && "$existing_origin" != "git@github.com:${target_repo}.git" ]]; then
    printf '%s\n' 'origin が指定先と異なるため中止しました。別のリポジトリへの変更は行いません。' >&2
    exit 1
  fi
  if [[ -n "$existing_origin" ]]; then
    validate_origin_push_urls
  fi
  current_branch="$(git symbolic-ref --short HEAD)"
  if [[ "$current_branch" != main ]]; then
    printf '現在のブランチは %s です。main ブランチで実行してください。\n' "$current_branch" >&2
    exit 1
  fi
fi

repo_exists=false
if gh repo view "$target_repo" --json nameWithOwner >/dev/null 2>&1; then
  repo_exists=true
  if [[ -z "$existing_origin" ]]; then
    printf '既存リポジトリ %s への誤った上書きを防ぐため中止しました。別の --name を指定してください。\n' "$target_repo" >&2
    exit 1
  fi
fi

printf 'GitHub アカウント: %s\n公開先: https://github.com/%s\n' "$account_login" "$target_repo"
if [[ "$dry_run" == true ]]; then
  printf '%s\n' '確認のみで終了しました。ファイル、Git、GitHub は変更していません。'
  exit 0
fi

python_command=python3
if [[ -x .venv/bin/python ]]; then python_command=.venv/bin/python; fi
"$python_command" -m unittest discover -s tests -v
node --check web/app.js
node --check web/worker.js
node scripts/build.mjs

if [[ ! -e .git ]]; then git init -b main; fi
if ! git config user.name >/dev/null; then git config user.name "$account_login"; fi
if ! git config user.email >/dev/null; then
  git config user.email "${account_id}+${account_login}@users.noreply.github.com"
fi
git add --all
if ! git diff --cached --quiet; then git commit -m 'Build Polynomial Lab'; fi
if [[ "$repo_exists" != true ]]; then
  gh repo create "$target_repo" --public --description '多項式の根・既約性をブラウザ内で計算する、読み上げ対応の静的Webアプリ'
fi
if [[ -z "$existing_origin" ]]; then git remote add origin "$target_url"; fi
validate_origin_push_urls
if ! git -c credential.helper= -c 'credential.https://github.com.helper=!gh auth git-credential' push --set-upstream origin main; then
  printf '%s\n' 'push を完了できませんでした。workflow 権限が不足する場合は gh auth refresh --hostname github.com --scopes workflow を実行し、再試行してください。force push は使用していません。' >&2
  exit 1
fi

if gh api "repos/${target_repo}/pages" >/dev/null 2>&1; then
  gh api --method PUT "repos/${target_repo}/pages" -f build_type=workflow >/dev/null
else
  gh api --method POST "repos/${target_repo}/pages" -f build_type=workflow >/dev/null
fi
gh workflow run pages.yml --repo "$target_repo" --ref main
printf '\n公開処理を開始しました。完了状況は次のコマンドで確認できます。\n'
printf 'gh run list --repo %s --workflow pages.yml\n' "$target_repo"
printf '公開完了後の URL: https://%s.github.io/%s/\n' "$account_login" "$repo_name"
