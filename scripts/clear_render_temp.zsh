#!/bin/zsh

set -eu

script_dir=${0:A:h}
project_root=${script_dir:h}
runs_root=${CBS_RUNS_ROOT:-$project_root/runs}
temp_root=${TMPDIR:-/tmp}
hyperframes_cache=${temp_root%/}/hyperframes-extract-cache-$(id -u)

if ps ax -o command= | grep -E '[h]yperframes.*render|clinic_broll\.(worker|cli).*step' >/dev/null; then
  echo "A Clinic B-roll/HyperFrames render is active; cleanup cancelled." >&2
  exit 1
fi

before_kb=$(df -k "$project_root" | awk 'NR==2 {print $4}')
removed=0

remove_temp_dir() {
  local target=$1
  if [[ -d "$target" ]]; then
    echo "Removing $target"
    rm -rf -- "$target"
    removed=$((removed + 1))
  fi
}

# Content-addressed source-frame cache. HyperFrames recreates it on demand.
remove_temp_dir "$hyperframes_cache"

if [[ -d "$runs_root" ]]; then
  # Interrupted renderer scratch directories under project-owned run folders.
  while IFS= read -r -d $'\0' target; do
    remove_temp_dir "$target"
  done < <(
    find "$runs_root" -type d -name 'work-*' \
      \( -path '*/renders/work-*' -o -path '*/previews/work-*' \) -print0
  )

  # Versioned overlay folders without a joined overlay are failed/incomplete.
  while IFS= read -r -d $'\0' target; do
    if [[ ! -f "$target/overlay.webm" ]]; then
      remove_temp_dir "$target"
    fi
  done < <(find "$runs_root" -type d -name 'final-overlay-v*' -path '*/renders/*' -print0)

  # Locks are advisory and safe to discard after the active-process check.
  find "$runs_root" -type f -name '*.lock' -delete
fi

after_kb=$(df -k "$project_root" | awk 'NR==2 {print $4}')
freed_kb=$((after_kb - before_kb))
freed_gb=$(awk -v kb="$freed_kb" 'BEGIN {printf "%.2f", kb / 1024 / 1024}')
available_gb=$(awk -v kb="$after_kb" 'BEGIN {printf "%.2f", kb / 1024 / 1024}')

echo "Cleanup complete: removed $removed temporary directorie(s), freed ${freed_gb} GiB."
echo "Available space: ${available_gb} GiB."
