#!/bin/bash

MAXIMUM_LINES=40
LARGEST_ENTRIES=10
TEMPORARY_FOLDER="${TMPDIR:-/tmp}"

print_largest_entries() {
    local folder="$1"
    if [[ ! -d "$folder" ]]; then
        printf 'not a folder: %s\n' "$folder"
        return
    fi
    find "${folder%/}/" -mindepth 1 -maxdepth 1 -exec du -sk {} + 2> /dev/null \
        | sort -rn \
        | head -n "$LARGEST_ENTRIES"
}

{
    df -h / "$TEMPORARY_FOLDER"
    print_largest_entries "${TMPDIR:-}"
    print_largest_entries /tmp
    print_largest_entries "${RUNNER_TEMP:-}"
    print_largest_entries "${HOME:-}"
} 2>&1 | head -n "$MAXIMUM_LINES"

exit 0
