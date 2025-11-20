"""Run a container from the image in a local OCI directory.

This adapter uses Skopeo to save a Docker image (or any source that Skopeo
supports) to a local directory that's compliant with the "Open Container Image
Layout Specification" and can be tracked as objects in a DataLad dataset.

This image can then be executed using one of several OCI runtimes:
- Apptainer: Executes directly from OCI directory (no daemon loading)
- Singularity: Executes directly from OCI directory (no daemon loading)
- Podman: Loads to containers-storage, then executes
- Docker: Loads to docker-daemon, then executes

The runtime is selected via 'datalad.containers-run.oci-runtime' config option,
which defaults to 'auto' (tries runtimes in the order listed above).

Examples
--------

Save BusyBox 1.32 from Docker Hub to the local directory bb_1.32:

    $ python -m datalad_container.adapters.oci \\
      save docker://busybox:1.32 bb-1.32/

Load the image into the Docker daemon (if necessary) and run a command:

    $ python -m datalad_container.adapters.oci \\
      run bb_1.32/ sh -c 'busybox | head -1'
    BusyBox v1.32.0 (2020-10-12 23:47:18 UTC) multi-call binary.
"""
# ^TODO: Add note about expected image ID mismatches (e.g., between the docker
# pulled entry and loaded one)?

from collections import namedtuple
import json
import logging
import os
from pathlib import Path
import re
from shutil import which
import subprocess as sp
import sys

from datalad_container.adapters.utils import (
    docker_run,
    get_docker_image_ids,
    log_and_exit,
    setup_logger,
)

lgr = logging.getLogger("datalad.container.adapters.oci")

_IMAGE_SOURCE_KEY = "org.datalad.container.image.source"


def detect_oci_runtime():
    """Detect which OCI runtime is available.

    Tries runtimes in order: apptainer, singularity, podman, docker

    Returns
    -------
    tuple of (str, str) or None
        (runtime_name, runtime_path) if found, None otherwise.
        Example: ("apptainer", "/usr/bin/apptainer")
    """
    for runtime in ["apptainer", "singularity", "podman", "docker"]:
        runtime_path = which(runtime)
        if runtime_path:
            lgr.debug("Detected OCI runtime: %s at %s", runtime, runtime_path)
            return (runtime, runtime_path)
    lgr.debug("No OCI runtime detected")
    return None


def get_oci_runtime(dataset_path=None):
    """Get OCI runtime from config or auto-detect.

    Reads configuration from 'datalad.containers-run.oci-runtime'.
    Precedence: Environment variable > Dataset config > User config > auto

    Parameters
    ----------
    dataset_path : str or Path, optional
        Path to dataset to read config from. If None, only reads user-level config.

    Returns
    -------
    tuple of (str, str)
        (runtime_name, runtime_path)
        Example: ("docker", "/usr/bin/docker")

    Raises
    ------
    RuntimeError
        If no runtime is available or configured runtime is not found.
    """
    config_key = "datalad.containers-run.oci-runtime"
    configured = None

    # Check environment variable first (highest priority)
    configured = os.environ.get("DATALAD_CONTAINERS_RUN_OCI_RUNTIME")
    if configured:
        lgr.debug("Using environment variable: DATALAD_CONTAINERS_RUN_OCI_RUNTIME = %s",
                 configured)
    else:
        # Try dataset-level config second
        if dataset_path:
            try:
                from datalad.api import Dataset
                ds = Dataset(dataset_path)
                configured = ds.config.get(config_key, default=None)
                if configured:
                    lgr.debug("Using dataset config: %s = %s", config_key, configured)
            except Exception as exc:
                lgr.debug("Could not read dataset config: %s", exc)

        # Fall back to user-level config third
        if configured is None:
            try:
                result = sp.run(
                    ["git", "config", "--get", config_key],
                    stdout=sp.PIPE, stderr=sp.PIPE,
                    universal_newlines=True, check=False
                )
                if result.returncode == 0:
                    configured = result.stdout.strip()
                    lgr.debug("Using user config: %s = %s", config_key, configured)
            except Exception as exc:
                lgr.debug("Could not read user config: %s", exc)

    # Default to auto if nothing configured
    if not configured:
        configured = "auto"
        lgr.debug("No config found, using default: auto")

    # Handle 'auto' mode - detect available runtime
    if configured == "auto":
        result = detect_oci_runtime()
        if not result:
            raise RuntimeError(
                "No OCI runtime found. Install one of: apptainer, singularity, podman, docker"
            )
        lgr.info("Auto-detected OCI runtime: %s", result[0])
        return result

    # User specified specific runtime - verify it exists
    runtime_path = which(configured)
    if not runtime_path:
        raise RuntimeError(
            f"Configured OCI runtime '{configured}' not found in PATH. "
            f"Available runtimes: {', '.join([r for r in ['apptainer', 'singularity', 'podman', 'docker'] if which(r)])}"
        )

    lgr.info("Using configured OCI runtime: %s", configured)
    return (configured, runtime_path)


def _normalize_reference(reference):
    """Normalize a short repository name to a canonical one.

    Parameters
    ----------
    reference : str
        A Docker reference (e.g., "neurodebian", "library/neurodebian").

    Returns
    -------
    A fully-qualified reference (e.g., "docker.io/library/neurodebian")

    Note: This tries to follow containers/image's splitDockerDomain().
    """
    parts = reference.split("/", maxsplit=1)
    if len(parts) == 1 or (not any(c in parts[0] for c in [".", ":"])
                           and parts[0] != "localhost"):
        domain, remainder = "docker.io", reference
    else:
        domain, remainder = parts

    if domain == "docker.io" and "/" not in remainder:
        remainder = "library/" + remainder
    return domain + "/" + remainder


Reference = namedtuple("Reference", ["name", "tag", "digest"])


def parse_docker_reference(reference, normalize=False, strip_transport=False):
    """Parse a Docker reference into a name, tag, and digest.

    Parameters
    ----------
    reference : str
        A Docker reference (e.g., "busybox" or "library/busybox:latest")
    normalize : bool, optional
        Whether to normalize short names like "busybox" to the fully qualified
        name ("docker.io/library/busybox")
    strip_transport : bool, optional
        Remove Skopeo transport value ("docker://" or "docker-daemon:") from
        the name. Unless this is true, reference should not include a
        transport.

    Returns
    -------
    A Reference namedtuple with .name, .tag, and .digest attributes
    """
    if strip_transport:
        try:
            reference = reference.split(":", maxsplit=1)[1]
        except IndexError:
            raise ValueError("Reference did not have transport: {}"
                             .format(reference))
        if reference.startswith("//"):
            reference = reference[2:]

    parts = reference.split("/")
    last = parts[-1]
    if "@" in last:
        sep = "@"
    elif ":" in last:
        sep = ":"
    else:
        sep = None

    tag = None
    digest = None
    if sep:
        repo, label = last.split(sep)
        front = "/".join(parts[:-1] + [repo])
        if sep == "@":
            digest = label
        else:
            tag = label
    else:
        front, tag = "/".join(parts), None
    if normalize:
        front = _normalize_reference(front)
    return Reference(front, tag, digest)


def _store_annotation(path, key, value):
    """Set a value of image's org.datalad.container.image.source annotation.

    Parameters
    ----------
    path : pathlib.Path
        Image directory. It must contain only one image.
    key, value : str
        Key and value to store in the image's "annotations" field.
    """
    index = path / "index.json"
    index_info = json.loads(index.read_text())
    annot = index_info["manifests"][0].get("annotations", {})

    annot[key] = value
    index_info["manifests"][0]["annotations"] = annot
    with index.open("w") as fh:
        json.dump(index_info, fh)


def _get_annotation(path, key):
    """Return value for `key` in an image's annotation.

    Parameters
    ----------
    path : pathlib.Path
        Image directory. It must contain only one image.
    key : str
        Key in the image's "annotations" field.

    Returns
    -------
    str or None
    """
    index = path / "index.json"
    index_info = json.loads(index.read_text())
    # Assume one manifest because skopeo-inspect would fail anyway otherwise.
    return index_info["manifests"][0].get("annotations", {}).get(key)


def save(image, path):
    """Save an image to an OCI-compliant directory.

    Parameters
    ----------
    image : str
        A source image accepted by skopeo-copy
    path : pathlib.Path
        Directory to copy the image to
    """
    # Refuse to work with non-empty directory if it's not empty by letting the
    # OSError through. Multiple images can be saved to an OCI directory, but
    # run() and get_image_id() don't support a way to pull out a specific one.
    try:
        path.rmdir()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise OSError(exc) from None
    path.mkdir(parents=True)
    dest = "oci:" + str(path)
    tag = parse_docker_reference(image).tag
    if tag:
        dest += ":" + tag
    sp.run(["skopeo", "copy", image, dest], check=True)
    _store_annotation(path, _IMAGE_SOURCE_KEY, image)


def link(ds, path, reference):
    """Add Docker registry URLs to annexed layer images.

    Parameters
    ----------
    ds : Dataset
    path : pathlib.Path
        Absolute path to the image directory.
    reference : str
        Docker reference (e.g., "busybox:1.32"). This should not include the
        transport (i.e. "docker://").
    """
    from datalad.downloaders.providers import Providers
    from datalad.support.exceptions import CommandError
    from datalad_container.utils import ensure_datalad_remote

    res = sp.run(["skopeo", "inspect", "oci:" + str(path)],
                 stdout=sp.PIPE, stderr=sp.PIPE,
                 universal_newlines=True, check=True)
    info = json.loads(res.stdout)

    ref = parse_docker_reference(reference, normalize=True)
    registry, name = ref.name.split("/", maxsplit=1)

    # Docker Hub has a special endpoint, all others follow the pattern
    # https://{registry}/v2/
    if registry == "docker.io":
        endpoint = "https://registry-1.docker.io/v2/"
    else:
        endpoint = f"https://{registry}/v2/"
    provider = Providers.from_config_files().get_provider(
        endpoint + name, only_nondefault=True)
    if not provider:
        lgr.warning("Required Datalad provider configuration "
                    "for Docker registry links not detected. We will enable 'datalad' "
                    "special remote anyways but datalad might issue warnings later on.")

    layers = {}  # path => digest
    for layer in info["Layers"]:
        algo, digest = layer.split(":")
        layer_path = path / "blobs" / algo / digest
        layers[layer_path] = layer

    ds_repo = ds.repo
    checked_dl_remote = False
    for st in ds.status(layers.keys(), annex="basic", result_renderer=None):
        if "keyname" in st:
            if not checked_dl_remote:
                ensure_datalad_remote(ds_repo)
                checked_dl_remote = True
            path = Path(st["path"])
            url = "{}{}/blobs/{}".format(endpoint, name, layers[path])
            try:
                ds_repo.add_url_to_file(
                    path, url, batch=True, options=['--relaxed'])
            except CommandError as exc:
                lgr.warning("Registering %s with %s failed: %s",
                            path, url, exc)
        else:
            lgr.warning("Skipping non-annexed layer: %s", st["path"])


def get_image_id(path):
    """Return a directory's image ID.

    Parameters
    ----------
    path : pathlib.Path
        Image directory. It must contain only one image.

    Returns
    -------
    An image ID (str)
    """
    # Note: This adapter depends on one image per directory. If, outside of
    # this adapter interface, multiple images were stored in a directory, this
    # will inspect call fails with a reasonable message.
    res = sp.run(["skopeo", "inspect", "--raw", "oci:" + str(path)],
                 stdout=sp.PIPE, stderr=sp.PIPE,
                 universal_newlines=True, check=True)
    info = json.loads(res.stdout)
    return info["config"]["digest"]


def apptainer_run(image_path, cmd):
    """Execute command using Apptainer with direct OCI support.

    Apptainer can execute directly from OCI directories without loading
    to a daemon first.

    Parameters
    ----------
    image_path : pathlib.Path
        Path to the OCI image directory
    cmd : list
        Command to execute in the container
    """
    lgr.debug("Executing with Apptainer from %s", image_path)
    apptainer_args = [
        "apptainer", "exec",
        "--pwd", os.getcwd(),  # Set working directory
        f"oci:{image_path}",
    ]
    apptainer_args.extend(cmd)
    sp.run(apptainer_args, check=True)


def singularity_run(image_path, cmd):
    """Execute command using Singularity with direct OCI support.

    Singularity can execute directly from OCI directories without loading
    to a daemon first.

    Parameters
    ----------
    image_path : pathlib.Path
        Path to the OCI image directory
    cmd : list
        Command to execute in the container
    """
    lgr.debug("Executing with Singularity from %s", image_path)
    singularity_args = [
        "singularity", "exec",
        "--pwd", os.getcwd(),  # Set working directory
        f"oci:{image_path}",
    ]
    singularity_args.extend(cmd)
    sp.run(singularity_args, check=True)


def podman_run(image_id, cmd):
    """Execute command using Podman.

    Similar to docker_run but uses podman command.

    Parameters
    ----------
    image_id : str
        Container image ID
    cmd : list
        Command to execute in the container
    """
    lgr.debug("Executing with Podman: %s", image_id)

    podman_args = [
        "podman", "run",
        "-v", os.getcwd() + ":/tmp:Z",
        "-w", "/tmp",
        "--rm",
    ]

    # Use Podman's keep-id for proper user namespace mapping (Linux only)
    # This maps the current user into the container instead of hardcoding UID
    if sys.platform != "win32":
        podman_args.extend(["--userns=keep-id"])

    # Add interactive mode
    podman_args.append("-i")

    # Add the image and command
    podman_args.extend([image_id] + cmd)

    sp.run(podman_args, check=True)


def load(path, runtime="docker"):
    """Load OCI image from `path` to daemon.

    Loads image to Docker daemon or Podman storage for execution.
    Apptainer and Singularity do not need this step as they execute directly
    from OCI directories.

    Parameters
    ----------
    path : pathlib.Path
        An OCI-compliant directory such as the one generated by `save`. It must
        contain only one image.
    runtime : str, optional
        Runtime to load for: "docker" or "podman". Default is "docker".

    Returns
    -------
    An image ID (str)
    """
    if runtime not in ["docker", "podman"]:
        raise ValueError(f"load() only supports docker and podman, got: {runtime}")

    image_id = get_image_id(path)

    # Determine the transport based on runtime
    if runtime == "podman":
        transport = "containers-storage"
        # Check if image exists in podman
        check_cmd = ["podman", "image", "exists", image_id]
        exists = sp.run(check_cmd, stdout=sp.PIPE, stderr=sp.PIPE).returncode == 0
    else:  # docker
        transport = "docker-daemon"
        exists = image_id in get_docker_image_ids()

    if not exists:
        lgr.debug("Loading %s", image_id)
        # The image is copied with a datalad-container/ prefix to reduce the
        # chance of collisions with existing names registered with the Docker
        # daemon. While we must specify _something_ for the name and tag in
        # order to copy it, the particular values don't matter for execution
        # purposes; they're chosen to help users identify the container in the
        # `docker images` output.
        source = _get_annotation(path, _IMAGE_SOURCE_KEY)
        if source:
            ref = parse_docker_reference(source, strip_transport=True)
            name = ref.name
            if ref.tag:
                tag = ref.tag
            else:
                if ref.digest:
                    tag = "source-" + ref.digest.replace(":", "-")[:14]
                else:
                    tag = "latest"

        else:
            name = re.sub("[^a-z0-9-_.]", "", path.name.lower()[:10])
            tag = image_id.replace(":", "-")[:14]

        lgr.debug("Copying %s to %s", image_id, runtime)
        sp.run(["skopeo", "copy", "oci:" + str(path),
                # This load happens right before the command executes. Don't
                # let the output be confused for the command's output.
                "--quiet",
                "{}:datalad-container/{}:{}".format(transport, name, tag)],
               check=True)
    else:
        lgr.debug("Image %s is already present", image_id)
    return image_id


# Command-line


def cli_save(namespace):
    save(namespace.image, namespace.path)


def cli_run(namespace):
    """Execute container command using configured or detected runtime.

    Parameters
    ----------
    namespace : argparse.Namespace
        Parsed command-line arguments with 'path' and 'cmd' attributes
    """
    # Determine the dataset path (parent of image directory)
    # This allows dataset-level config to override
    image_path = namespace.path
    dataset_path = None

    # Try to find dataset root by looking for .datalad directory
    current = image_path.resolve().parent
    while current != current.parent:
        if (current / ".datalad").is_dir():
            dataset_path = current
            lgr.debug("Found dataset at: %s", dataset_path)
            break
        current = current.parent

    # Get the configured or detected runtime
    runtime_name, runtime_path = get_oci_runtime(dataset_path)

    # Execute based on runtime type
    if runtime_name == "apptainer":
        apptainer_run(image_path, namespace.cmd)
    elif runtime_name == "singularity":
        singularity_run(image_path, namespace.cmd)
    elif runtime_name == "podman":
        # Podman needs to load the image first (similar to Docker)
        image_id = load(image_path, runtime="podman")
        podman_run(image_id, namespace.cmd)
    elif runtime_name == "docker":
        # Docker needs to load the image first
        image_id = load(image_path, runtime="docker")
        docker_run(image_id, namespace.cmd)
    else:
        raise RuntimeError(f"Unsupported runtime: {runtime_name}")


def main(args):
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m datalad_container.adapters.oci",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "-v", "--verbose",
        action="store_true")

    subparsers = parser.add_subparsers(title="subcommands")
    # Don't continue without a subcommand.
    subparsers.required = True
    subparsers.dest = "command"

    parser_save = subparsers.add_parser(
        "save",
        help="save an image to a directory")
    parser_save.add_argument(
        "image", metavar="NAME",
        help="image to save")
    parser_save.add_argument(
        "path", metavar="PATH", type=Path,
        help="directory to save image in")
    parser_save.set_defaults(func=cli_save)

    parser_run = subparsers.add_parser(
        "run",
        help="run a command with a directory's image")

    # TODO: Support containers-storage/podman. This would need to be fed
    # through cli_run() and load(). Also, a way to specify it should probably
    # be available through containers-add.
    # parser_run.add_argument(
    #     "--dest", metavar="TRANSPORT",
    #     choices=["docker-daemon", "containers-storage"],
    #     ...)
    parser_run.add_argument(
        "path", metavar="PATH", type=Path,
        help="image directory")
    parser_run.add_argument(
        "cmd", metavar="CMD", nargs=argparse.REMAINDER,
        help="command to execute")
    parser_run.set_defaults(func=cli_run)

    namespace = parser.parse_args(args[1:])

    setup_logger(logging.DEBUG if namespace.verbose else logging.INFO)

    namespace.func(namespace)


if __name__ == "__main__":
    with log_and_exit(lgr):
        main(sys.argv)
