#!/usr/bin/env bash
#
# reconstruct-history.sh - rebuild a plausible, backdated commit timeline.
#
# Creates empty commits (by design: a lost history has no surviving per-commit
# diffs) with synchronized GIT_AUTHOR_DATE / GIT_COMMITTER_DATE values, a
# realistic week-by-week cadence, and Conventional Commit messages that progress
# from bootstrap to architecture, features, fixes, refactors and perf work.
#
# Intended use: a fresh `git init` (optionally holding the current working
# tree), on a dedicated branch. Nothing is pushed and no remote is touched.
#
# Requires: bash 3.2+ (macOS default), git. Works with GNU and BSD date.
#
# Usage:
#   ./reconstruct-history.sh --repo ../my-project --start 2025-01-01
#   ./reconstruct-history.sh --repo . --branch history --dry-run --seed 42
#
set -euo pipefail

REPO="."
BRANCH=""
START="2025-01-01"
END="$(date +%Y-%m-%d)"
SEED=""
DRY_RUN=0
AUTHOR_NAME=""
AUTHOR_EMAIL=""

usage() {
  cat <<'EOF'
Usage: reconstruct-history.sh [options]

Options:
  --repo <path>       Target git repository (default: .)
  --branch <name>     Create/switch to this branch before committing
  --start <YYYY-MM-DD> First day of the timeline (default: 2025-01-01)
  --end <YYYY-MM-DD>   Last day of the timeline (default: today)
  --seed <0-32767>    Reproducible schedule (printed when omitted)
  --author-name <s>   Author/committer name (default: git config user.name)
  --author-email <s>  Author/committer email (default: git config user.email)
  --dry-run           Print the plan without creating commits
  -h, --help          Show this help

The script refuses to run if the index has staged changes, because
`git commit --allow-empty` would otherwise absorb them into a commit.
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --repo) REPO="${2-}"; shift 2 ;;
    --branch) BRANCH="${2-}"; shift 2 ;;
    --start) START="${2-}"; shift 2 ;;
    --end) END="${2-}"; shift 2 ;;
    --seed) SEED="${2-}"; shift 2 ;;
    --author-name) AUTHOR_NAME="${2-}"; shift 2 ;;
    --author-email) AUTHOR_EMAIL="${2-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

[[ "$START" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "invalid --start: $START"
[[ "$END" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "invalid --end: $END"
[[ "$START" < "$END" || "$START" == "$END" ]] || die "--start must not be after --end"
command -v git >/dev/null 2>&1 || die "git is not on PATH"
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  die "$REPO is not a git repository"

# ---------------------------------------------------------------- date layer
# GNU date and BSD date disagree on everything useful. Probe once.
if date -d "2000-01-01 12:00:00" +%s >/dev/null 2>&1; then
  DATE_STYLE="gnu"
else
  DATE_STYLE="bsd"
fi

day_meta() { # YYYY-MM-DD -> "weekday|utc-offset" (weekday 0=Sunday)
  if [[ "$DATE_STYLE" == "gnu" ]]; then
    date -d "$1 12:00:00" +"%w|%z"
  else
    date -j -f "%Y-%m-%d %H:%M:%S" "$1 12:00:00" +"%w|%z"
  fi
}

next_day() { # YYYY-MM-DD -> next calendar day
  if [[ "$DATE_STYLE" == "gnu" ]]; then
    date -d "$1 12:00:00 +1 day" +%Y-%m-%d
  else
    date -j -v+1d -f "%Y-%m-%d %H:%M:%S" "$1 12:00:00" +%Y-%m-%d
  fi
}

day_meta "$START" >/dev/null 2>&1 || die "invalid calendar date for --start: $START"
day_meta "$END" >/dev/null 2>&1 || die "invalid calendar date for --end: $END"

# ------------------------------------------------------------------ git prep
if [[ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ]]; then
  printf 'warning: worktree is not clean; only untracked files are harmless.\n' >&2
fi
if ! git -C "$REPO" diff --cached --quiet 2>/dev/null; then
  die "the index has staged changes; commit or unstage them first"
fi

CURRENT_BRANCH="$(git -C "$REPO" symbolic-ref --short -q HEAD || true)"
if [[ -n "$BRANCH" ]]; then
  if git -C "$REPO" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$REPO" switch "$BRANCH" >/dev/null 2>&1 ||
      git -C "$REPO" checkout "$BRANCH" >/dev/null
  else
    git -C "$REPO" switch -c "$BRANCH" >/dev/null 2>&1 ||
      git -C "$REPO" checkout -b "$BRANCH" >/dev/null
  fi
  CURRENT_BRANCH="$BRANCH"
fi
if [[ -z "$CURRENT_BRANCH" ]]; then
  die "detached HEAD; pass --branch to create a branch for the new commits"
fi

if [[ -z "$AUTHOR_NAME" ]]; then
  AUTHOR_NAME="$(git -C "$REPO" config user.name 2>/dev/null || true)"
fi
if [[ -z "$AUTHOR_EMAIL" ]]; then
  AUTHOR_EMAIL="$(git -C "$REPO" config user.email 2>/dev/null || true)"
fi
[[ -n "$AUTHOR_NAME" ]] || die "no author name; set user.name or pass --author-name"
[[ -n "$AUTHOR_EMAIL" ]] || die "no author email; set user.email or pass --author-email"

# ------------------------------------------------------------------- random
if [[ -z "$SEED" ]]; then
  SEED=$(( (RANDOM << 8 ^ RANDOM) & 32767 ))
fi
[[ "$SEED" =~ ^[0-9]+$ ]] || die "invalid --seed: $SEED"
(( SEED >= 0 && SEED <= 32767 )) || die "--seed must be between 0 and 32767"
RANDOM=$(( SEED & 32767 ))

rand_int() { RAND=$(( $1 + RANDOM % ($2 - $1 + 1) )); }
pick_elem() { # sets PICKED from the named array
  local name="$1" size idx
  eval "size=\${#$name[@]}"
  (( size > 0 )) || die "internal: empty pool $name"
  idx=$(( RANDOM % size ))
  eval "PICKED=\${$name[$idx]}"
}

TODAY="$(date +%Y-%m-%d)"
NOW_H=$((10#$(date +%H)))
NOW_M=$((10#$(date +%M)))

# --------------------------------------------------------------- day series
DAYS=(); DOWS=(); OFFS=()
d="$START"
while :; do
  meta="$(day_meta "$d")" || die "date conversion failed for $d"
  DAYS+=("$d")
  DOWS+=("$((10#${meta%%|*}))")
  OFFS+=("${meta#*|}")
  if [[ "$d" == "$END" ]]; then break; fi
  d="$(next_day "$d")" || die "date conversion failed after $d"
  if [[ "$d" > "$END" ]]; then break; fi
done

WEEKS=()
cur=""
for i in "${!DAYS[@]}"; do
  if [[ "${DOWS[$i]}" -eq 1 && -n "$cur" ]]; then
    WEEKS+=("$cur"); cur=""
  fi
  if [[ -z "$cur" ]]; then cur="$i"; else cur="$cur,$i"; fi
done
if [[ -n "$cur" ]]; then WEEKS+=("$cur"); fi
NUM_WEEKS=${#WEEKS[@]}

# One deliberate vacation gap of ~1-2 weeks makes the timeline breathe.
VACATION_START=-1
if (( NUM_WEEKS > 12 )); then
  rand_int $(( NUM_WEEKS / 3 )) $(( NUM_WEEKS * 2 / 3 ))
  VACATION_START=$RAND
fi

# --------------------------------------------------------- schedule the days
TIMES=()
for w in "${!WEEKS[@]}"; do
  IFS=',' read -r -a WEEK_DAYS <<< "${WEEKS[$w]}"
  week_len=${#WEEK_DAYS[@]}
  progress=100
  if (( NUM_WEEKS > 1 )); then progress=$(( 100 * w / (NUM_WEEKS - 1) )); fi

  sprint_p=12; quiet_p=24
  if (( progress >= 15 && progress <= 55 )); then sprint_p=30; quiet_p=12; fi
  if (( progress > 80 )); then sprint_p=15; quiet_p=28; fi

  n=0
  if (( w == 0 )); then
    n=2
  elif (( VACATION_START >= 0 && (w == VACATION_START || w == VACATION_START + 1) )); then
    n=0
  else
    r=$(( RANDOM % 100 ))
    if (( r < sprint_p )); then
      rand_int 3 6; n=$RAND
    elif (( r < sprint_p + quiet_p )); then
      n=0
    else
      rand_int 1 2; n=$RAND
    fi
  fi
  if (( n > week_len )); then n=$week_len; fi

  WD=(); WE=()
  for idx in "${WEEK_DAYS[@]}"; do
    if (( DOWS[idx] == 0 || DOWS[idx] == 6 )); then WE+=("$idx"); else WD+=("$idx"); fi
  done

  POOL=()
  if (( n > 0 )); then
    if (( n <= ${#WD[@]} && (n >= 3 || RANDOM % 100 < 85) )); then
      POOL=("${WD[@]}")
    else
      POOL=("${WEEK_DAYS[@]}")
    fi
    # Fisher-Yates; order inside the week is irrelevant because we sort later.
    for (( i=${#POOL[@]}-1; i>0; i-- )); do
      j=$(( RANDOM % (i + 1) ))
      tmp="${POOL[$i]}"; POOL[$i]="${POOL[$j]}"; POOL[$j]="$tmp"
    done
  fi

  for (( k=0; k<n; k++ )); do
    idx="${POOL[$k]}"
    dow="${DOWS[$idx]}"
    if (( dow == 0 || dow == 6 )); then
      if (( RANDOM % 100 < 25 )); then rand_int 10 13; else rand_int 14 22; fi
      hour=$RAND
    else
      r=$(( RANDOM % 100 ))
      if (( r < 45 )); then
        rand_int 16 20
      elif (( r < 70 )); then
        rand_int 20 23
      elif (( r < 82 )); then
        rand_int 0 2
      elif (( r < 92 )); then
        rand_int 9 12
      else
        rand_int 13 15
      fi
      hour=$RAND
    fi
    rand_int 0 59; minute=$RAND

    # Never schedule a commit in the future.
    if [[ "${DAYS[$idx]}" > "$TODAY" ]]; then continue; fi
    if [[ "${DAYS[$idx]}" == "$TODAY" ]] && (( hour > NOW_H || (hour == NOW_H && minute > NOW_M) )); then
      continue
    fi
    printf -v ts '%sT%02d:%02d:00%s' "${DAYS[$idx]}" "$hour" "$minute" "${OFFS[$idx]}"
    TIMES+=("$ts")
  done
done

(( ${#TIMES[@]} > 0 )) || die "the generated range contains no schedulable commits"

SORTED=()
while IFS= read -r line; do SORTED+=("$line"); done < <(printf '%s\n' "${TIMES[@]}" | LC_ALL=C sort)

# --------------------------------------------------------------- message pool
SCOPES=(core api cli ui storage auth config worker cache search export scheduler)
FEATURE_PAIRS=(
  'api|cursor-based pagination'
  'api|batch import endpoint'
  'api|idempotency keys'
  'api|rate limiting'
  'api|request tracing'
  'api|role-based access control'
  'auth|token refresh flow'
  'auth|session invalidation'
  'cache|content-addressed storage'
  'cli|interactive init command'
  'cli|shell completions'
  'config|environment validation'
  'core|structured logging'
  'core|feature flags'
  'core|graceful shutdown'
  'core|health check endpoint'
  'export|CSV export'
  'export|JSON export'
  'search|filtered queries'
  'search|fuzzy matching'
  'storage|connection pooling'
  'storage|schema migrations'
  'ui|dark mode toggle'
  'ui|keyboard navigation'
  'ui|loading skeletons'
  'ui|responsive layout'
  'ui|error boundaries'
  'worker|retry with backoff'
  'worker|scheduled cleanup'
  'notifications|webhook delivery'
)
ARCH_PAIRS=(
  'core|application entry point'
  'core|domain models'
  'core|module boundaries'
  'api|request routing skeleton'
  'storage|repository abstraction'
  'config|environment loading'
  'auth|session middleware'
  'worker|job queue interface'
  'ui|component library baseline'
  'test|test harness'
)
BUG_PAIRS=(
  'api|empty request bodies'
  'api|pagination offset drift'
  'api|concurrent write collisions'
  'api|optional fields bypassing validation'
  'auth|expired token refresh loop'
  'cache|stale entries after invalidation'
  'cli|config precedence'
  'core|timer leaks on shutdown'
  'core|ignored cancellation signals'
  'export|truncated long values'
  'parser|malformed unicode input'
  'scheduler|timezone drift'
  'search|case sensitivity mismatch'
  'storage|stale reads after update'
  'storage|connection leak under load'
  'ui|focus loss after modal close'
  'ui|layout overflow on narrow screens'
  'ui|deep link reset on reload'
  'worker|duplicate delivery on retry'
  'worker|transient failure retry storm'
)
UNIT_PAIRS=(
  'api|route registration'
  'api|middleware chain'
  'api|serialization layer'
  'api|error responses'
  'auth|session handling'
  'cache|eviction policy'
  'cli|argument parsing'
  'config|schema validation'
  'core|config loader'
  'core|error types'
  'core|event bus'
  'core|logging adapter'
  'core|dependency wiring'
  'parser|tokenizer'
  'release|build script'
  'storage|migration runner'
  'storage|query builder'
  'storage|repository interface'
  'harness|fixtures'
  'ui|data fetching hooks'
  'ui|form state'
  'ui|modal component'
  'ui|theme tokens'
  'worker|job dispatcher'
  'worker|scheduler'
)
METRICS=(
  'p95 request latency'
  'cold start time'
  'memory footprint'
  'bundle size'
  'query time on large datasets'
  'startup time'
  'render time for long lists'
  'log volume'
  'test runtime'
  'build time'
)
TOPICS=(
  'local development setup'
  'configuration options'
  'error handling conventions'
  'API versioning'
  'deployment process'
  'testing strategy'
  'architecture overview'
  'contributing guidelines'
  'release checklist'
  'troubleshooting section'
  'migration notes'
  'security model'
  'data retention policy'
  'CLI usage'
  'plugin interface'
  'performance baseline'
  'observability stack'
  'backup and restore'
  'environment variables'
  'upgrade guide'
)
CHORE_UNITS=(
  'dead code'
  'unused imports'
  'stale TODOs'
  'legacy config'
  'duplicate helpers'
  'debug logging'
  'unused assets'
  'obsolete scripts'
  'lint warnings'
  'flaky test annotations'
)
DEPS=(
  'runtime dependencies'
  'dev dependencies'
  'lockfile'
  'toolchain'
  'test framework'
  'linter'
  'SDK'
  'database driver'
)
BUILD_TARGETS=(
  'linting'
  'formatting'
  'bundling'
  'type checking'
  'test runner'
  'container image'
  'release artifacts'
  'source maps'
)
CI_TARGETS=(
  'CI'
  'release'
  'dependency audit'
  'nightly'
  'coverage'
  'docs'
)
BOOTSTRAP_MSGS=(
  'chore: add editorconfig and gitignore'
  'chore: initialize package metadata'
  'chore: pin toolchain version'
  'build: configure linting and formatting'
  'build: add type checking pipeline'
  'ci: add CI workflow'
  'docs: add README skeleton'
  'docs: document local development setup'
  'chore(deps): pin baseline dependencies'
)

split_pair() { PAIR_SCOPE="${1%%|*}"; PAIR_THING="${1#*|}"; }
add_used() { USED+=("$1"); }
is_used() {
  local candidate="$1" u
  for u in "${USED[@]}"; do
    if [[ "$u" == "$candidate" ]]; then return 0; fi
  done
  return 1
}

phase_for() { # 0..6 based on index / total
  local pct=$(( 100 * $1 / $2 ))
  if (( pct < 5 )); then echo 0
  elif (( pct < 16 )); then echo 1
  elif (( pct < 45 )); then echo 2
  elif (( pct < 65 )); then echo 3
  elif (( pct < 80 )); then echo 4
  elif (( pct < 94 )); then echo 5
  else echo 6
  fi
}

choose_type() { # sets CHOSEN_TYPE
  local phase="$1" total=0 r acc=0 i
  local -a types weights
  case "$phase" in
    1) types=(feat refactor test docs chore); weights=(40 15 15 15 15) ;;
    2) types=(feat test fix chore); weights=(65 15 10 10) ;;
    3) types=(fix test refactor docs); weights=(55 20 15 10) ;;
    4) types=(refactor test docs chore); weights=(45 25 15 15) ;;
    5) types=(perf fix refactor test); weights=(45 20 15 20) ;;
    6) types=(docs chore perf fix test); weights=(30 25 15 15 15) ;;
    *) types=(chore build ci docs); weights=(30 25 20 25) ;;
  esac
  for i in "${!weights[@]}"; do total=$(( total + weights[i] )); done
  r=$(( RANDOM % total ))
  for i in "${!types[@]}"; do
    acc=$(( acc + weights[i] ))
    if (( r < acc )); then CHOSEN_TYPE="${types[i]}"; return; fi
  done
  CHOSEN_TYPE="${types[0]}"
}

compose_message() { # $1 type, $2 phase; sets MSG
  local type="$1" phase="$2"
  MSG=""
  case "$type" in
    feat)
      if (( phase == 1 )); then
        pick_elem ARCH_PAIRS; split_pair "$PICKED"
        if (( RANDOM % 2 )); then
          MSG="feat($PAIR_SCOPE): add $PAIR_THING"
        else
          MSG="feat($PAIR_SCOPE): establish $PAIR_THING"
        fi
      else
        pick_elem FEATURE_PAIRS; split_pair "$PICKED"
        case $(( RANDOM % 3 )) in
          0) MSG="feat($PAIR_SCOPE): add $PAIR_THING" ;;
          1) MSG="feat($PAIR_SCOPE): support $PAIR_THING" ;;
          2) MSG="feat($PAIR_SCOPE): implement $PAIR_THING" ;;
        esac
      fi
      ;;
    fix)
      pick_elem BUG_PAIRS; split_pair "$PICKED"
      case $(( RANDOM % 3 )) in
        0) MSG="fix($PAIR_SCOPE): handle $PAIR_THING" ;;
        1) MSG="fix($PAIR_SCOPE): prevent $PAIR_THING" ;;
        2) MSG="fix($PAIR_SCOPE): guard against $PAIR_THING" ;;
      esac
      ;;
    refactor)
      pick_elem UNIT_PAIRS; split_pair "$PICKED"
      case $(( RANDOM % 3 )) in
        0) MSG="refactor($PAIR_SCOPE): extract $PAIR_THING" ;;
        1) MSG="refactor($PAIR_SCOPE): simplify $PAIR_THING" ;;
        2) MSG="refactor($PAIR_SCOPE): consolidate $PAIR_THING" ;;
      esac
      ;;
    perf)
      case $(( RANDOM % 4 )) in
        0)
          pick_elem SCOPES
          scope="$PICKED"
          pick_elem METRICS
          MSG="perf($scope): reduce $PICKED"
          ;;
        1)
          pick_elem UNIT_PAIRS; split_pair "$PICKED"
          MSG="perf($PAIR_SCOPE): cache $PAIR_THING"
          ;;
        2)
          pick_elem UNIT_PAIRS; split_pair "$PICKED"
          MSG="perf($PAIR_SCOPE): batch $PAIR_THING lookups"
          ;;
        3)
          pick_elem UNIT_PAIRS; split_pair "$PICKED"
          MSG="perf: avoid redundant work in $PAIR_THING"
          ;;
      esac
      ;;
    test)
      case $(( RANDOM % 3 )) in
        0)
          pick_elem FEATURE_PAIRS; split_pair "$PICKED"
          MSG="test($PAIR_SCOPE): cover $PAIR_THING"
          ;;
        1)
          pick_elem BUG_PAIRS; split_pair "$PICKED"
          MSG="test($PAIR_SCOPE): add regression case for $PAIR_THING"
          ;;
        2)
          pick_elem UNIT_PAIRS; split_pair "$PICKED"
          MSG="test($PAIR_SCOPE): strengthen $PAIR_THING coverage"
          ;;
      esac
      ;;
    docs)
      pick_elem TOPICS
      case $(( RANDOM % 4 )) in
        0) MSG="docs: document $PICKED" ;;
        1) MSG="docs: expand $PICKED" ;;
        2) MSG="docs: clarify $PICKED" ;;
        3) MSG="docs: update $PICKED" ;;
      esac
      ;;
    chore)
      case $(( RANDOM % 3 )) in
        0)
          pick_elem CHORE_UNITS
          MSG="chore: remove $PICKED"
          ;;
        1)
          pick_elem UNIT_PAIRS; split_pair "$PICKED"
          MSG="chore($PAIR_SCOPE): tidy $PAIR_THING"
          ;;
        2)
          pick_elem DEPS
          MSG="chore(deps): bump $PICKED"
          ;;
      esac
      ;;
    build)
      pick_elem BUILD_TARGETS
      if (( RANDOM % 2 )); then MSG="build: configure $PICKED"; else MSG="build: add $PICKED to pipeline"; fi
      ;;
    ci)
      pick_elem CI_TARGETS
      if (( RANDOM % 2 )); then MSG="ci: add $PICKED workflow"; else MSG="ci: cache $PICKED artifacts"; fi
      ;;
  esac
}

compose_unique() { # $1 type, $2 phase; sets MSG
  local attempt=0
  while :; do
    compose_message "$1" "$2"
    if [[ -n "$MSG" ]] && ! is_used "$MSG"; then return; fi
    attempt=$(( attempt + 1 ))
    # Exhausted the pool for this slot: allow a repeat rather than loop forever.
    if (( attempt >= 60 )); then return; fi
  done
}

# --------------------------------------------------------------- emit commits
USED=()
TOTAL=${#SORTED[@]}
if (( DRY_RUN )); then
  printf 'dry run: %d commits on %s (%s .. %s), seed %s\n' \
    "$TOTAL" "${CURRENT_BRANCH:-HEAD}" "$START" "$END" "$SEED"
else
  printf 'creating %d commits on %s (%s .. %s), seed %s\n' \
    "$TOTAL" "${CURRENT_BRANCH:-HEAD}" "$START" "$END" "$SEED"
fi

for i in "${!SORTED[@]}"; do
  ts="${SORTED[$i]}"
  if (( i == 0 )); then
    msg="chore: bootstrap project scaffolding"
  else
    phase="$(phase_for "$i" "$TOTAL")"
    if (( phase == 0 )); then
      attempt=0
      while :; do
        pick_elem BOOTSTRAP_MSGS
        msg="$PICKED"
        is_used "$msg" || break
        attempt=$(( attempt + 1 ))
        if (( attempt >= 40 )); then break; fi
      done
    else
      choose_type "$phase"
      compose_unique "$CHOSEN_TYPE" "$phase"
      msg="$MSG"
    fi
  fi
  add_used "$msg"

  printf '  %s  %s\n' "$ts" "$msg"
  if (( DRY_RUN )); then continue; fi

  GIT_AUTHOR_DATE="$ts" GIT_COMMITTER_DATE="$ts" \
  GIT_AUTHOR_NAME="$AUTHOR_NAME" GIT_AUTHOR_EMAIL="$AUTHOR_EMAIL" \
  GIT_COMMITTER_NAME="$AUTHOR_NAME" GIT_COMMITTER_EMAIL="$AUTHOR_EMAIL" \
    git -C "$REPO" -c commit.gpgsign=false commit \
      --allow-empty --no-verify --quiet -m "$msg"
done

if (( DRY_RUN )); then
  printf 'planned: %d commits authored and committed at identical timestamps\n' "$TOTAL"
else
  printf 'done: %d commits authored and committed at identical timestamps\n' "$TOTAL"
fi
printf 'review with: git -C "%s" log --graph --oneline --date=iso\n' "$REPO"
if (( ! DRY_RUN )) && [[ -n "$(git -C "$REPO" remote 2>/dev/null || true)" ]]; then
  printf 'push with:   git -C "%s" push -u origin %s\n' "$REPO" "${CURRENT_BRANCH:-HEAD}"
fi
