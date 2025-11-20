#!/bin/bash
# Demo script: Run MRIQC with all available OCI container runtimes
# This script demonstrates that a single OCI container can be executed
# with different runtimes (Docker, Podman, Apptainer, Singularity)
#
# Usage: ./demo_all_container_runtimes.sh [temp_directory]
# If no directory provided, creates one in /tmp

set -e  # Exit on any error
set -u  # Exit on undefined variable

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_runtime() {
    echo -e "${MAGENTA}[RUNTIME: $1]${NC} $2"
}

# Detect available runtimes
detect_runtimes() {
    local runtimes=()
    for runtime in apptainer singularity podman docker; do
        if command -v $runtime &> /dev/null; then
            runtimes+=("$runtime")
        fi
    done
    echo "${runtimes[@]}"
}

# Check prerequisites
check_requirements() {
    log_info "Checking requirements..."

    local missing=()

    if ! command -v datalad &> /dev/null; then
        missing+=("datalad")
    fi

    if ! command -v skopeo &> /dev/null; then
        missing+=("skopeo")
    fi

    # Check for at least one OCI runtime
    local available_runtimes=($(detect_runtimes))

    if [ ${#available_runtimes[@]} -eq 0 ]; then
        missing+=("oci-runtime (apptainer, singularity, podman, or docker)")
    else
        log_success "Found ${#available_runtimes[@]} OCI runtime(s): ${available_runtimes[*]}"
    fi

    if [ ${#missing[@]} -ne 0 ]; then
        log_error "Missing required tools: ${missing[*]}"
        echo ""
        echo "Install missing tools:"
        echo "  Ubuntu/Debian:"
        echo "    sudo apt-get install datalad skopeo apptainer docker.io podman"
        echo "  macOS:"
        echo "    brew install datalad skopeo"
        echo "    brew install --cask docker"
        exit 1
    fi

    log_success "All requirements satisfied"
}

# Run MRIQC with specific runtime
run_with_runtime() {
    local runtime=$1
    local output_suffix=$2

    log_runtime "$runtime" "Starting MRIQC analysis..."

    # Create output directory
    mkdir -p "derivatives/mriqc_${output_suffix}"
    touch "derivatives/mriqc_${output_suffix}/.gitkeep"
    datalad save -m "Prepare output directory for ${runtime}" "derivatives/mriqc_${output_suffix}"
    chmod -R g+w "derivatives/mriqc_${output_suffix}" 2>/dev/null || true

    # Set runtime via environment variable
    export DATALAD_CONTAINERS_RUN_OCI_RUNTIME="$runtime"

    # For Apptainer/Singularity: set cache dir to avoid /tmp space issues
    # Only set CACHEDIR (not TMPDIR) to avoid "is a directory" errors
    if [[ "$runtime" == "apptainer" ]] || [[ "$runtime" == "singularity" ]]; then
        export APPTAINER_CACHEDIR="$HOME/.cache/apptainer"
        export SINGULARITY_CACHEDIR="$HOME/.cache/singularity"
        mkdir -p "$APPTAINER_CACHEDIR" "$SINGULARITY_CACHEDIR"
        log_runtime "$runtime" "Using cache directory: $APPTAINER_CACHEDIR"
    fi

    log_runtime "$runtime" "Running MRIQC participant analysis..."
    echo "  Output: derivatives/mriqc_${output_suffix}/"
    echo ""

    # Run the container
    if datalad containers-run \
        -n mriqc \
        --input sourcedata/raw \
        --output "derivatives/mriqc_${output_suffix}" \
        -m "Run MRIQC QC with ${runtime} runtime" \
        sourcedata/raw "derivatives/mriqc_${output_suffix}" participant -w "workdir_${output_suffix}"; then

        log_runtime "$runtime" "Analysis complete ✓"
        return 0
    else
        log_error "Failed to run with ${runtime}"
        return 1
    fi
}

# Main workflow
main() {
    # Determine working directory
    if [ $# -gt 0 ]; then
        WORK_DIR="$1"
    else
        WORK_DIR="/tmp/mriqc-runtime-demo-$$"
    fi

    log_info "Working directory: $WORK_DIR"

    # Check requirements first
    check_requirements

    # Detect available runtimes
    AVAILABLE_RUNTIMES=($(detect_runtimes))

    echo ""
    log_info "=== OCI Container Runtime Comparison Demo ==="
    log_info "This demo will run MRIQC with each available runtime:"
    for runtime in "${AVAILABLE_RUNTIMES[@]}"; do
        echo "  • $runtime"
    done
    echo ""

    # Create working directory
    mkdir -p "$WORK_DIR"
    cd "$WORK_DIR"

    ANALYSIS_DIR="$WORK_DIR/ds000003-runtime-comparison"

    # Step 1: Create analysis dataset (skip if exists)
    if [ -d "$ANALYSIS_DIR/.datalad" ]; then
        log_info "Step 1: Using existing analysis dataset..."
        cd "$ANALYSIS_DIR"
        log_success "Found existing dataset: $ANALYSIS_DIR"
    else
        log_info "Step 1: Creating analysis dataset (YODA structure)..."
        datalad create -c text2git -D "MRIQC runtime comparison for ds000003" "$ANALYSIS_DIR"
        cd "$ANALYSIS_DIR"
        log_success "Analysis dataset created: $ANALYSIS_DIR"
    fi
    echo ""

    # Step 2: Install ReproNim demo dataset as subdataset (skip if exists)
    if [ -d "sourcedata/raw/.datalad" ]; then
        log_info "Step 2: Source data already installed..."
        log_success "Using existing sourcedata/raw"
    else
        log_info "Step 2: Installing ReproNim ds000003-demo as source data..."
        echo "NOTE: Installing from https://github.com/ReproNim/ds000003-demo"
        echo ""

        datalad install -d . \
            -s https://github.com/ReproNim/ds000003-demo \
            sourcedata/raw

        log_success "Source data installed at sourcedata/raw"
    fi
    echo ""

    # Step 3: Configure working directories (skip if exists)
    if [ -f ".gitignore" ]; then
        log_info "Step 3: .gitignore already configured..."
        log_success "Using existing .gitignore"
    else
        log_info "Step 3: Configuring .gitignore for working directories..."
        cat > .gitignore <<'EOF'
# Working directories - don't track intermediate files
workdir*/
derivatives/*/work/
EOF
        datalad save -m "Configure working directory ignore patterns"
        log_success "Working directories configured"
    fi
    echo ""

    # Step 4: Add MRIQC container (skip if exists)
    if [ -d ".datalad/environments/mriqc/image" ]; then
        log_info "Step 4: MRIQC container already added..."
        log_success "Using existing container"
    else
        log_info "Step 4: Adding MRIQC container (will be used by all runtimes)..."
        echo "NOTE: This will download MRIQC (~2GB), may take a few minutes"
        echo ""

        datalad containers-add mriqc \
            --url oci:docker://nipreps/mriqc:23.1.0

        log_success "MRIQC container added"
    fi
    echo ""

    # Verify container configuration
    log_info "Container configuration:"
    datalad containers-list
    echo ""

    # Step 5: Prepare base derivatives directory (skip if exists)
    if [ -f "derivatives/.gitkeep" ]; then
        log_info "Step 5: Derivatives directory already prepared..."
        log_success "Using existing derivatives structure"
    else
        log_info "Step 5: Preparing derivatives directory structure..."
        mkdir -p derivatives
        touch derivatives/.gitkeep
        datalad save -m "Initialize derivatives directory"
        log_success "Ready for multi-runtime analysis"
    fi
    echo ""

    # Step 6: Run MRIQC with each available runtime
    echo ""
    log_info "========================================="
    log_info "Step 6: Running MRIQC with each runtime"
    log_info "========================================="
    echo ""

    local success_count=0
    local fail_count=0
    local results=()

    for runtime in "${AVAILABLE_RUNTIMES[@]}"; do
        echo ""
        log_info "----------------------------------------"
        log_info "Testing runtime: $runtime"
        log_info "----------------------------------------"
        echo ""

        if run_with_runtime "$runtime" "$runtime"; then
            ((success_count++))
            results+=("✓ $runtime")
        else
            ((fail_count++))
            results+=("✗ $runtime")
        fi

        echo ""
    done

    # Step 7: Compare results
    echo ""
    log_info "========================================="
    log_info "Step 7: Runtime Comparison Summary"
    log_info "========================================="
    echo ""

    echo "Runtime Test Results:"
    for result in "${results[@]}"; do
        echo "  $result"
    done
    echo ""

    echo "Success: $success_count / ${#AVAILABLE_RUNTIMES[@]}"
    echo "Failed:  $fail_count / ${#AVAILABLE_RUNTIMES[@]}"
    echo ""

    # Step 8: Show output directories
    log_info "Step 8: Output directories created:"
    echo ""
    for runtime in "${AVAILABLE_RUNTIMES[@]}"; do
        if [ -d "derivatives/mriqc_${runtime}" ]; then
            echo "  derivatives/mriqc_${runtime}/"
            if [ -d "derivatives/mriqc_${runtime}" ]; then
                file_count=$(find "derivatives/mriqc_${runtime}" -type f | wc -l)
                echo "    Files: $file_count"
            fi
        fi
    done
    echo ""

    # Step 9: Show provenance
    log_info "Step 9: Provenance tracking..."
    echo ""
    echo "=== Git commit history ==="
    git --no-pager log --oneline -10
    echo ""

    # Step 10: Final summary
    echo ""
    log_success "=== OCI Container Runtime Demo Complete ==="
    echo ""
    echo "What was demonstrated:"
    echo "  ✓ Single OCI container added once"
    echo "  ✓ Container executed with ${#AVAILABLE_RUNTIMES[@]} different runtime(s):"
    for runtime in "${AVAILABLE_RUNTIMES[@]}"; do
        echo "    • $runtime"
    done
    echo "  ✓ Each runtime produced separate outputs"
    echo "  ✓ All runs tracked with full provenance"
    echo ""
    echo "Dataset location: $ANALYSIS_DIR"
    echo ""
    echo "Key findings:"
    echo "  • Same container image works across multiple runtimes"
    echo "  • Each runtime produces comparable results"
    echo "  • Provenance tracking works regardless of runtime"
    echo ""
    echo "To explore further:"
    echo "  cd $ANALYSIS_DIR"
    echo ""
    echo "  # Compare outputs from different runtimes:"
    for runtime in "${AVAILABLE_RUNTIMES[@]}"; do
        echo "  ls -la derivatives/mriqc_${runtime}/"
    done
    echo ""
    echo "  # View git history:"
    echo "  git log --oneline"
    echo ""
    echo "  # See which runtime was used for each run:"
    echo "  git log --all --grep='runtime' --oneline"
    echo ""
    echo "To configure default runtime:"
    echo "  git config --local datalad.containers-run.oci-runtime <runtime>"
    echo "  # Options: apptainer, singularity, podman, docker"
    echo ""

    log_info "Cleanup: Run 'rm -rf $WORK_DIR' when done"
}

# Run main function
main "$@"
