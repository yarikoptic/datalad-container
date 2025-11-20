OCI Container Support
*********************

DataLad-container supports working with OCI (Open Container Initiative) compliant images using `Skopeo <https://github.com/containers/skopeo>`_. This provides an efficient way to track container images as DataLad dataset objects with full version control and reproducibility.

Overview
========

The OCI adapter uses Skopeo to save container images as OCI-compliant directory structures that can be tracked by git-annex. This approach offers several advantages:

- **Version Control**: Container images are stored as trackable objects in your DataLad dataset
- **Efficient Storage**: Container layers are stored separately and can be retrieved from registries on-demand via git-annex
- **Registry Flexibility**: Works with Docker Hub, quay.io, ghcr.io, gcr.io, and other OCI-compliant registries
- **Reproducibility**: Exact container versions are tracked and can be re-obtained

The OCI adapter differs from the ``dhub://`` scheme by storing images in an OCI-compliant directory format rather than as Docker tar archives. This format is more efficient for version control and enables direct integration with container registries for remote retrieval.

Requirements
============

To use OCI container support, you need:

- **Skopeo**: Install Skopeo for your platform:

  - **Debian/Ubuntu**: ``sudo apt-get install skopeo``
  - **Fedora/RHEL**: ``sudo dnf install skopeo``
  - **macOS**: ``brew install skopeo``
  - **Other platforms**: See `Skopeo installation docs <https://github.com/containers/skopeo/blob/main/install.md>`_

- **Container Runtime** (at least one of the following):

  - **Apptainer** (recommended for HPC): ``sudo apt-get install apptainer`` or see `Apptainer docs <https://apptainer.org/docs/admin/main/installation.html>`_
  - **Singularity**: See `Singularity docs <https://sylabs.io/docs/>`_
  - **Podman**: ``sudo apt-get install podman`` or see `Podman docs <https://podman.io/getting-started/installation>`_
  - **Docker**: See `Docker installation <https://docs.docker.com/get-docker/>`_

- **Git-annex**: Version 8.20210903 or later recommended for best results

The OCI adapter will auto-detect available runtimes and use them in this priority order: Apptainer → Singularity → Podman → Docker. You can configure a specific runtime using the ``datalad.containers-run.oci-runtime`` configuration option (see `Runtime Configuration`_ below).

Basic Usage
===========

Adding an OCI Container
-----------------------

Use the ``oci:docker://`` URL scheme to add a container from a registry::

    datalad containers-add --url oci:docker://busybox:1.30 my-local-busybox

This will:

1. Download the image using Skopeo
2. Store it as an OCI-compliant directory in ``.datalad/environments/my-container/image``
3. Configure the container for execution via the OCI adapter
4. Track all files with git-annex

Running Commands in OCI Containers
-----------------------------------

Execute commands in the container using ``containers-run``::

    datalad containers-run -n my-local-busybox echo "Hello from container"

The container image will be automatically loaded into the Docker daemon (if not already present) before execution.

Supported Container Registries
---------=====================

Docker Hub
----------

Docker Hub is the default registry. Short names are automatically expanded::

    # These are equivalent:
    datalad containers-add -n myapp -u oci:docker://busybox:1.30
    datalad containers-add -n myapp -u oci:docker://library/busybox:1.30
    datalad containers-add -n myapp -u oci:docker://docker.io/library/busybox:1.30

Quay.io
-------

Use the full registry path for Quay.io images::

    datalad containers-add -n prometheus \
        -u oci:docker://quay.io/prometheus/prometheus:latest

GitHub Container Registry (ghcr.io)
------------------------------------

Access images from GitHub Container Registry::

    datalad containers-add -n uv \
        -u oci:docker://ghcr.io/astral-sh/uv:latest

Google Container Registry (gcr.io)
-----------------------------------

Use images from Google Container Registry::

    datalad containers-add -n busybox-gcr \
        -u oci:docker://gcr.io/google-containers/busybox:latest

Advanced Features
=================

Using Digests for Reproducibility
----------------------------------

For maximum reproducibility, use digest references instead of tags::

    datalad containers-add -n fixed-version \
        -u oci:docker://busybox@sha256:a9286defaba7b3a519d585ba0e37d0b2cbee74ebfe590960b0b1d6a5e97d1e1d

Digest references guarantee the exact same image content, regardless of tag updates.

Registry URL Linking
--------------------

When adding containers with ``oci:docker://`` URLs, DataLad automatically registers registry URLs with git-annex for each container layer. This enables efficient remote retrieval::

    # Add container (layers are linked to registry)
    datalad containers-add -n myapp -u oci:docker://busybox:1.30

    # Drop local content to save space
    datalad drop .datalad/environments/myapp/image

    # Get content back from registry when needed
    datalad get .datalad/environments/myapp/image

    # Run container (will retrieve from registry if needed)
    datalad containers-run -n myapp echo "Hello"

For this feature to work with Docker Hub, you should configure a DataLad provider with your registry credentials. See `Provider Configuration`_ below.

Updating OCI Containers
------------------------

Update an existing container to a new version::

    datalad containers-add -n my-local-container --url oci:docker://busybox:1.31 --update

This will remove the old version and add the new one, recording the change in your dataset history.

Runtime Configuration
=======================

The OCI adapter supports multiple container runtimes for executing containers. By default, it auto-detects available runtimes in the following order: **Apptainer**, **Singularity**, **Podman**, and **Docker**.

Supported Runtimes
------------------

- **Apptainer/Singularity**: Execute directly from OCI directories (no daemon loading required)
- **Podman**: Loads images to containers-storage, then executes
- **Docker**: Loads images to docker-daemon, then executes (traditional method)

Configuring the Runtime
-----------------------

You can configure which runtime to use at different levels:

**Environment variable** (highest priority - temporary override)::

    export DATALAD_CONTAINERS_RUN_OCI_RUNTIME=singularity
    datalad containers-run -n myapp echo "Hello"

**Dataset-level configuration** (recommended for project-specific settings)::

    # Inside your dataset
    git config --local datalad.containers-run.oci-runtime apptainer

**User-level configuration** (global default for all datasets)::

    # Global setting for all datasets
    git config --global datalad.containers-run.oci-runtime podman

**Configuration values:**

- ``auto`` (default): Auto-detect runtime in order: apptainer → singularity → podman → docker
- ``apptainer``: Force use of Apptainer
- ``singularity``: Force use of Singularity
- ``podman``: Force use of Podman
- ``docker``: Force use of Docker

**Priority order**: Environment variable > Dataset-level config > User-level config > ``auto``

Runtime-Specific Behavior
--------------------------

**Apptainer and Singularity**:
  These runtimes execute directly from the OCI directory without loading to a daemon first. This provides:

  - Faster execution (no daemon loading step)
  - No persistent daemon images (saves disk space)
  - Direct OCI directory access

  Example::

      # Configure to use Apptainer
      git config datalad.containers-run.oci-runtime apptainer

      # Run container (executes directly from OCI directory)
      datalad containers-run -n myapp echo "Hello"

**Podman**:
  Similar to Docker, but uses Podman's containers-storage. The image is loaded once and cached.

  Example::

      git config datalad.containers-run.oci-runtime podman
      datalad containers-run -n myapp echo "Hello"

**Docker**:
  Loads the image to the Docker daemon on first use. Subsequent runs reuse the cached image.

  Example::

      git config datalad.containers-run.oci-runtime docker
      datalad containers-run -n myapp echo "Hello"

.. note::
   For Apptainer/Singularity users: These runtimes offer better integration with HPC environments and don't require a running daemon, making them ideal for shared computing resources.

Provider Configuration
======================

For optimal integration with Docker registries (especially Docker Hub), configure a DataLad provider with your credentials.

Docker Hub Provider Setup
-------------------------

Create or edit ``~/.config/datalad/providers/<provider-name>.cfg``::

    [provider:docker-hub]
    url_re = https://registry-1\.docker\.io/v2/.*
    authentication_type = http_basic_auth
    credential = docker-hub

Then configure credentials using ``git-annex``::

    git annex enableremote datalad
    git config annex.security.allowed-ip-addresses all

Or use the credential manager of your choice.

.. note::
   Provider configuration is optional for basic functionality but recommended for:

   - Private images requiring authentication
   - Reliable layer retrieval from registries
   - Avoiding rate limits

For other registries (quay.io, ghcr.io, etc.), create similar provider configurations with the appropriate URL patterns and authentication methods.

Comparison: OCI vs. Docker Hub Adapter
=======================================

DataLad-container offers two ways to work with Docker images: ``oci:`` and ``dhub://``. Here's when to use each:

Use ``oci:docker://`` when:
----------------------------

- You want efficient version control of container images
- You need to work with multiple registries (quay.io, ghcr.io, gcr.io)
- You want git-annex integration for remote layer retrieval
- You have Skopeo installed
- Storage efficiency is important (OCI layers can be deduplicated by git-annex)

**Example**::

    datalad containers-add -n myapp -u oci:docker://busybox:1.30

**Pros:**
- Better integration with DataLad's version control
- Efficient remote retrieval via git-annex
- Works with multiple registries
- OCI-compliant storage format

**Cons:**
- Requires Skopeo
- Slightly more complex setup

Use ``dhub://`` when:
---------------------

- You only need Docker Hub images
- You prefer a simpler setup (only requires Docker)
- You don't need layer-level remote retrieval

**Example**::

    datalad containers-add -n myapp -u dhub://busybox:1.30

**Pros:**
- Simple setup (only needs Docker)
- Direct tar archive storage

**Cons:**
- Only works with Docker Hub
- No layer-level git-annex integration
- Less efficient for version control

Troubleshooting
===============

Skopeo Not Found
----------------

**Error:** ``Command 'skopeo' not found``

**Solution:** Install Skopeo for your platform (see Requirements_ section above).

Docker Daemon Not Running
--------------------------

**Error:** ``Cannot connect to the Docker daemon``

**Solution:** Start the Docker daemon:

- **Linux**: ``sudo systemctl start docker``
- **macOS/Windows**: Start Docker Desktop

Image Load Failures
-------------------

**Error:** ``Failed to load image``

**Possible causes:**

1. Docker daemon not running
2. Insufficient disk space
3. Corrupted OCI directory

**Solutions:**

- Verify Docker is running: ``docker ps``
- Check disk space: ``df -h``
- Re-fetch the container: ``datalad get --force <image-path>``

Registry Authentication Issues
------------------------------

**Error:** ``Error reading manifest``

**Solution:** Configure authentication for private images:

1. Authenticate with the registry: ``docker login <registry>``
2. Configure DataLad provider (see `Provider Configuration`_ above)
3. For Docker Hub: Ensure you're not hitting rate limits

Layer Retrieval Failures
-------------------------

**Error:** ``Unable to retrieve layer from registry``

**Possible causes:**

1. Network connectivity issues
2. Missing provider configuration
3. Registry URL not registered with git-annex

**Solutions:**

- Check network connectivity
- Verify provider configuration (see `Provider Configuration`_)
- Re-add the container to re-register URLs

Known Limitations
=================

Current limitations of OCI container support:

- **One image per directory**: Each container requires its own directory
- **Docker daemon required**: Execution currently requires Docker (Podman support planned)
- **Fixed mount points**: Docker run mounts current directory to ``/tmp`` (configurable in future)
- **Image ID mismatches**: Loaded images may show different IDs than originals (cosmetic only)

Technical Details
=================

Storage Format
--------------

OCI containers are stored as directories compliant with the `OCI Image Layout Specification <https://github.com/opencontainers/image-spec/blob/main/image-layout.md>`_::

    .datalad/environments/<name>/image/
    ├── blobs/
    │   └── sha256/
    │       ├── <layer-digest>
    │       └── ...
    ├── index.json
    └── oci-layout

Execution Flow
--------------

When running a container:

1. DataLad reads the container configuration
2. OCI adapter checks if image is loaded in Docker daemon
3. If not loaded, Skopeo copies from OCI directory to Docker daemon
4. Docker executes the command with configured parameters
5. Results are returned to DataLad

Metadata Storage
----------------

The OCI adapter stores source metadata in the image's annotations::

    {
      "manifests": [{
        "annotations": {
          "org.datalad.container.image.source": "docker://busybox:1.30"
        }
      }]
    }

This enables tracking of the original source even after the image is loaded and potentially retagged.

Examples
========

Complete Workflow Example
-------------------------

Here's a complete example of adding, using, and managing an OCI container::

    # Create a dataset
    datalad create -c text2git myproject
    cd myproject

    # Add a container
    datalad containers-add -n analysis \
        -u oci:docker://python:3.11-slim

    # Run analysis script
    datalad containers-run -n analysis \
        --input data.csv \
        --output results.txt \
        python analyze.py

    # Drop local container to save space
    datalad drop .datalad/environments/analysis/image

    # Clone on another machine
    datalad clone /path/to/myproject myproject-clone
    cd myproject-clone

    # Re-run analysis (will fetch container automatically)
    datalad rerun

Multi-Registry Workflow
-----------------------

Working with images from different registries::

    # Add various containers
    datalad containers-add -n base \
        -u oci:docker://debian:stable-slim

    datalad containers-add -n python \
        -u oci:docker://ghcr.io/astral-sh/uv:latest

    datalad containers-add -n prometheus \
        -u oci:docker://quay.io/prometheus/prometheus:latest

    # List all containers
    datalad containers-list

    # Run commands in different containers
    datalad containers-run -n base apt-get update
    datalad containers-run -n python uv pip list
    datalad containers-run -n prometheus promtool --version

See Also
========

- `DataLad Handbook: Containers chapter <https://handbook.datalad.org/r?containers>`_
- `Skopeo Documentation <https://github.com/containers/skopeo/blob/main/docs/skopeo.1.md>`_
- `OCI Image Specification <https://github.com/opencontainers/image-spec>`_
- :doc:`DataLad documentation <index>`
