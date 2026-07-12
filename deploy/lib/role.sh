#!/usr/bin/env bash
# Scrapeboard — machine role (panel | worker) + role-based sparse-checkout
#
# Persist at repo / app root as .scrapeboard-role (gitignored).
# Override with SCRAPEBOARD_ROLE=panel|worker.
#
# Panel checkout: everything except worker/
# Worker checkout: root install helpers + worker/; exclude panel/ and deploy/
set -euo pipefail

ROLE_FILE_NAME="${ROLE_FILE_NAME:-.scrapeboard-role}"

normalize_scrapeboard_role() {
  local r
  r="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  case "$r" in
    panel|worker) printf '%s' "$r" ;;
    *) return 1 ;;
  esac
}

role_file_path() {
  local root="${1:-${APP_DIR:-${SCRAPEBOARD_ROOT:-.}}}"
  printf '%s/%s' "${root%/}" "$ROLE_FILE_NAME"
}

# Resolve role: SCRAPEBOARD_ROLE env wins, else role file. Empty if unset.
read_scrapeboard_role() {
  local root="${1:-${APP_DIR:-${SCRAPEBOARD_ROOT:-.}}}"
  local from_env="" from_file="" line
  if [[ -n "${SCRAPEBOARD_ROLE:-}" ]]; then
    if from_env="$(normalize_scrapeboard_role "$SCRAPEBOARD_ROLE")"; then
      printf '%s' "$from_env"
      return 0
    fi
    echo "ERROR: invalid SCRAPEBOARD_ROLE='${SCRAPEBOARD_ROLE}' (want panel|worker)" >&2
    return 1
  fi
  local f
  f="$(role_file_path "$root")"
  if [[ -f "$f" ]]; then
    line="$(tr -d '[:space:]' <"$f" | head -1 || true)"
    if from_file="$(normalize_scrapeboard_role "$line")"; then
      printf '%s' "$from_file"
      return 0
    fi
    echo "ERROR: invalid role in ${f}: '${line}' (want panel|worker)" >&2
    return 1
  fi
  return 1
}

write_scrapeboard_role() {
  local role root f
  role="$(normalize_scrapeboard_role "${1:?role required}")" || {
    echo "ERROR: write_scrapeboard_role: invalid role '${1}'" >&2
    return 1
  }
  root="${2:-${APP_DIR:-${SCRAPEBOARD_ROOT:-.}}}"
  f="$(role_file_path "$root")"
  mkdir -p "$root"
  printf '%s\n' "$role" >"$f"
  echo "==> Machine role: ${role} (${f})"
}

# Require expected role. If unset and ALLOW_SET_ROLE=1, write expected.
# If mismatch, fail with a clear message (unless FORCE_ROLE_SWITCH=1).
assert_scrapeboard_role() {
  local expected root current f line
  expected="$(normalize_scrapeboard_role "${1:?expected role}")" || return 1
  root="${2:-${APP_DIR:-${SCRAPEBOARD_ROOT:-.}}}"
  f="$(role_file_path "$root")"
  current=""

  if [[ -n "${SCRAPEBOARD_ROLE:-}" ]]; then
    current="$(normalize_scrapeboard_role "$SCRAPEBOARD_ROLE")" || {
      echo "ERROR: invalid SCRAPEBOARD_ROLE='${SCRAPEBOARD_ROLE}' (want panel|worker)" >&2
      return 1
    }
  elif [[ -f "$f" ]]; then
    line="$(tr -d '[:space:]' <"$f" | head -1 || true)"
    current="$(normalize_scrapeboard_role "$line")" || {
      echo "ERROR: invalid role in ${f}: '${line}' (want panel|worker)" >&2
      return 1
    }
  fi

  if [[ -n "$current" ]]; then
    if [[ "$current" == "$expected" ]]; then
      return 0
    fi
    if [[ "${FORCE_ROLE_SWITCH:-0}" == "1" ]]; then
      echo "==> Switching machine role ${current} → ${expected} (FORCE_ROLE_SWITCH=1)"
      write_scrapeboard_role "$expected" "$root"
      return 0
    fi
    cat >&2 <<EOF
ERROR: this machine is marked as '${current}' (${f} or SCRAPEBOARD_ROLE).
Refusing to run '${expected}' install/update here.

  Reconfigure: SCRAPEBOARD_ROLE=${expected} FORCE_ROLE_SWITCH=1 <command>
  Or edit/remove: ${f}
EOF
    return 1
  fi

  # No role yet
  if [[ "${ALLOW_SET_ROLE:-1}" == "1" ]]; then
    write_scrapeboard_role "$expected" "$root"
    return 0
  fi
  cat >&2 <<EOF
ERROR: machine role not set (expected '${expected}').
  Write ${f} with: panel  or  worker
  Or: SCRAPEBOARD_ROLE=${expected} <command>
EOF
  return 1
}

_git_as_site_user() {
  # Run git in APP_DIR as SITE_USER when set (Hestia), else as current user.
  local -a git_cmd=(git -C "${APP_DIR:?APP_DIR required}")
  if [[ -n "${SITE_USER:-}" ]] && command -v sudo >/dev/null 2>&1 && [[ "$(id -u)" -eq 0 ]]; then
    sudo -u "${SITE_USER}" "${git_cmd[@]}" "$@"
  else
    "${git_cmd[@]}" "$@"
  fi
}

_write_sparse_patterns() {
  local role="$1"
  local tmp
  tmp="$(mktemp)"
  case "$role" in
    panel)
      # Keep full tree except scrape worker runtime
      cat >"$tmp" <<'EOF'
/*
!/worker/
EOF
      ;;
    worker)
      # Keep worker + root install helpers; exclude panel and Hestia deploy tree
      cat >"$tmp" <<'EOF'
/*
!/panel/
!/deploy/
EOF
      ;;
    *)
      rm -f "$tmp"
      echo "ERROR: unknown role for sparse-checkout: ${role}" >&2
      return 1
      ;;
  esac
  if [[ -n "${SITE_USER:-}" ]] && command -v sudo >/dev/null 2>&1 && [[ "$(id -u)" -eq 0 ]]; then
    sudo -u "${SITE_USER}" tee "${APP_DIR}/.git/info/sparse-checkout" >/dev/null <"$tmp"
  else
    cat "$tmp" >"${APP_DIR}/.git/info/sparse-checkout"
  fi
  rm -f "$tmp"
}

enable_role_sparse_checkout() {
  local role="${1:?role}"
  [[ -d "${APP_DIR}/.git" ]] || return 0
  echo "==> ${role} sparse-checkout..."
  if ! _git_as_site_user sparse-checkout init --no-cone 2>/dev/null; then
    echo "    (warn) sparse-checkout unavailable; will prune forbidden paths after sync"
    return 0
  fi
  case "$role" in
    panel)
      if _git_as_site_user sparse-checkout set --no-cone '/*' '!/worker/' 2>/dev/null; then
        return 0
      fi
      ;;
    worker)
      if _git_as_site_user sparse-checkout set --no-cone '/*' '!/panel/' '!/deploy/' 2>/dev/null; then
        return 0
      fi
      ;;
  esac
  _write_sparse_patterns "$role"
  _git_as_site_user sparse-checkout reapply 2>/dev/null \
    || _git_as_site_user read-tree -mu HEAD 2>/dev/null \
    || true
}

ensure_role_tree() {
  local role="${1:?role}"
  case "$role" in
    panel)
      if [[ -e "${APP_DIR}/worker" ]]; then
        echo "==> Removing ${APP_DIR}/worker (panel role — workers belong on scrape hosts)"
        rm -rf "${APP_DIR}/worker"
      fi
      ;;
    worker)
      if [[ -e "${APP_DIR}/panel" ]]; then
        echo "==> Removing ${APP_DIR}/panel (worker role — control panel belongs on panel host)"
        rm -rf "${APP_DIR}/panel"
      fi
      if [[ -e "${APP_DIR}/deploy" ]]; then
        echo "==> Removing ${APP_DIR}/deploy (worker role — Hestia deploy is panel-only)"
        rm -rf "${APP_DIR}/deploy"
      fi
      ;;
  esac
}
