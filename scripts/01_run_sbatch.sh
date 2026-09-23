#!/bin/bash
#
# Author:
# Martin Schilling, 2025, martin.schilling@med.uni-goettingen.de
#
# Submit an input sbatch script to the cluster and archive the JobID.
# Option for saving the script and metadata in a given archive directory.

SCRIPT_DIR="$( cd "$( dirname "$(readlink -f "${BASH_SOURCE[0]}")" )" >/dev/null 2>&1 && pwd )"

helpstr=$(cat <<- EOF
Submit a batch job and create a log file containing the job id.
The sbatch script should follow the format YYYY-MM-DD_sbatch_<suffix>.sbatch.

-m move	Move the job files into the archive instead of copying them
-a archive	Directory for archiving sbatch script and log file
-r repos	File listing the git repositories to snapshot
-s settings	Settings file which names the HPC user in the metadata
-d jobid	Start the job only after this JobID completed successfully
-h help

The JobID of the submitted job is printed as 'SUBMITTED_JOBID=<jobid>'.
EOF
)

REPO_OPTION=()
SETTINGS_OPTION=()
MOVE_OPTION=()
SBATCH_OPTS=()

usage="Usage: $0 [-h] [-m move] [-a archive_dir] [-r repository_file] [-s settings_file] [-d jobid] <sbatch_file>"

while getopts "ma:r:s:d:h" opt; do
	case $opt in
	m)
		MOVE_OPTION+=(-m)
	;;
	a)
		ARCHIVE_DIR=$(readlink -f "$OPTARG")
	;;
	r)
		REPOSITORY_FILE=$(readlink -f "$OPTARG")
		REPO_OPTION+=(-r "$REPOSITORY_FILE")
	;;
	s)
		SETTINGS_OPTION+=(-s "$(readlink -f "$OPTARG")")
	;;
	d)
		# Only run after the dependency succeeded, and give up instead of waiting forever.
		SBATCH_OPTS+=(--dependency=afterok:"$OPTARG" --kill-on-invalid-dep=yes)
	;;
	h)
		echo "$usage"
		echo
		echo "$helpstr"
		exit 0
	;;
	\?)
		echo "$usage" >&2
		exit 1
	;;
	esac
done

shift $((OPTIND - 1))

if [ $# -lt 1 ] ; then

	echo "$usage" >&2
	exit 1
fi

INPUT=$(readlink -f "$1")

INPUT_DIR="$( cd "$( dirname "$INPUT" )" >/dev/null 2>&1 && pwd )"

# --- Handle file name ---
FILE_NAME=$(basename "$INPUT")
FILE_NAME=${FILE_NAME%.*}

IFS='_' read -r -a CONTENT <<< "$FILE_NAME"
DATE="${CONTENT[0]}"

read -ra SUFFIX_ARR <<< "${CONTENT[@]:2}"
SUFFIX_STR=$(IFS='_' ; echo "${SUFFIX_ARR[*]}")

LOG_FILE="$DATE"_log_"$SUFFIX_STR".txt
LOG_FILE="$INPUT_DIR"/"$LOG_FILE"

# --- Submit job ---
# '--parsable' prints the bare JobID, optionally followed by ';<cluster>'.
if ! JOB_STRING=$(sbatch --parsable "${SBATCH_OPTS[@]}" "$INPUT") ; then
	echo "Submission of $INPUT failed." >&2
	exit 1
fi

JOB_ID=${JOB_STRING%%;*}

if [ -z "$JOB_ID" ] ; then
	echo "No JobID was returned for $INPUT." >&2
	exit 1
fi

echo "SUBMITTED_JOBID=$JOB_ID"

if ! [ -f "$LOG_FILE" ] ; then
	printf '%s\n' "$JOB_ID" > "$LOG_FILE"
else
	# append to log file
	sed -i '$a'"$JOB_ID"'' "$LOG_FILE"
fi

if [ "$ARCHIVE_DIR" ] ; then
	echo "writing metadata"
	bash "$SCRIPT_DIR"/02_archive_scripts.sh "${MOVE_OPTION[@]}" -a "$ARCHIVE_DIR" -i "$INPUT_DIR" "$DATE" "$SUFFIX_STR"
	python "$SCRIPT_DIR"/write_metadata.py "${REPO_OPTION[@]}" "${SETTINGS_OPTION[@]}" "$ARCHIVE_DIR"/"$DATE"_"$SUFFIX_STR"/
fi
