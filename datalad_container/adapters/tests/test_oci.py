"""Test of oci adapter that do not depend on skopeo or docker being installed.


See datalad_container.adapters.tests.test_oci_more for tests that do depend on
this.
"""

import json
import os
from unittest.mock import patch

import pytest

from datalad.utils import Path
from datalad_container.adapters import oci
from datalad.tests.utils_pytest import (
    assert_raises,
    eq_,
    with_tempfile,
)

# parse_docker_reference


def test_parse_docker_reference():
    eq_(oci.parse_docker_reference("neurodebian").name,
        "neurodebian")


def test_parse_docker_reference_normalize():
    fn = oci.parse_docker_reference
    for name in ["neurodebian",
                 "library/neurodebian",
                 "docker.io/neurodebian"]:
        eq_(fn(name, normalize=True).name,
            "docker.io/library/neurodebian")

    eq_(fn("quay.io/skopeo/stable", normalize=True).name,
        "quay.io/skopeo/stable")
    eq_(fn("ghcr.io/astral-sh/uv", normalize=True).name,
        "ghcr.io/astral-sh/uv")
    eq_(fn("gcr.io/my-project/my-image", normalize=True).name,
        "gcr.io/my-project/my-image")


def test_parse_docker_reference_tag():
    fn = oci.parse_docker_reference
    eq_(fn("busybox:1.32"),
        ("busybox", "1.32", None))
    eq_(fn("busybox:1.32", normalize=True),
        ("docker.io/library/busybox", "1.32", None))
    eq_(fn("docker.io/library/busybox:1.32"),
        ("docker.io/library/busybox", "1.32", None))


@pytest.mark.ai_generated
def test_parse_docker_reference_alternative_registries():
    """Test parsing references from alternative registries like quay.io and ghcr.io."""
    fn = oci.parse_docker_reference

    # Test quay.io with tag
    eq_(fn("quay.io/linuxserver.io/baseimage-alpine:3.18"),
        ("quay.io/linuxserver.io/baseimage-alpine", "3.18", None))

    # Test ghcr.io with tag
    eq_(fn("ghcr.io/astral-sh/uv:latest"),
        ("ghcr.io/astral-sh/uv", "latest", None))

    # Test gcr.io with tag
    eq_(fn("gcr.io/my-project/my-image:v1.0"),
        ("gcr.io/my-project/my-image", "v1.0", None))


def test_parse_docker_reference_digest():
    fn = oci.parse_docker_reference
    id_ = "sha256:a9286defaba7b3a519d585ba0e37d0b2cbee74ebfe590960b0b1d6a5e97d1e1d"
    eq_(fn("busybox@{}".format(id_)),
        ("busybox", None, id_))
    eq_(fn("busybox@{}".format(id_), normalize=True),
        ("docker.io/library/busybox", None, id_))
    eq_(fn("docker.io/library/busybox@{}".format(id_)),
        ("docker.io/library/busybox", None, id_))


def test_parse_docker_reference_strip_transport():
    fn = oci.parse_docker_reference
    eq_(fn("docker://neurodebian", strip_transport=True).name,
        "neurodebian")
    eq_(fn("docker-daemon:neurodebian", strip_transport=True).name,
        "neurodebian")


def test_parse_docker_reference_strip_transport_no_transport():
    with assert_raises(ValueError):
        oci.parse_docker_reference("neurodebian", strip_transport=True)


# _store_annotation and _get_annotation

# This is the index.json contents of oci: copy of
# docker.io/library/busybox:1.32
INDEX_VALUE = {
    "schemaVersion": 2,
    "manifests": [
        {"mediaType": "application/vnd.oci.image.manifest.v1+json",
         "digest": "sha256:9f9f95fc6f6b24f0ab756a55b8326e8849ac6a82623bea29fc4c75b99ee166a3",
         "size": 347}]}


@with_tempfile(mkdir=True)
def test_store_and_get_annotation(path=None):
    path = Path(path)
    with (path / "index.json").open("w") as fh:
        json.dump(INDEX_VALUE, fh)

    eq_(oci._get_annotation(path, "org.opencontainers.image.ref.name"),
        None)

    oci._store_annotation(path, "org.opencontainers.image.ref.name", "1.32")
    eq_(oci._get_annotation(path, "org.opencontainers.image.ref.name"),
        "1.32")

    oci._store_annotation(path, "another", "foo")
    eq_(oci._get_annotation(path, "another"),
        "foo")
    eq_(oci._get_annotation(path, "org.opencontainers.image.ref.name"),
        "1.32")


# Runtime detection tests


def test_detect_oci_runtime_no_runtimes():
    """Test runtime detection when no runtimes are available."""
    with patch('datalad_container.adapters.oci.which', return_value=None):
        result = oci.detect_oci_runtime()
        eq_(result, None)


def test_detect_oci_runtime_finds_apptainer():
    """Test runtime detection finds apptainer first."""
    def mock_which(cmd):
        if cmd == "apptainer":
            return "/usr/bin/apptainer"
        return None

    with patch('datalad_container.adapters.oci.which', side_effect=mock_which):
        result = oci.detect_oci_runtime()
        eq_(result, ("apptainer", "/usr/bin/apptainer"))


def test_detect_oci_runtime_order():
    """Test runtime detection follows correct priority order."""
    def mock_which(cmd):
        # Only docker available
        if cmd == "docker":
            return "/usr/bin/docker"
        return None

    with patch('datalad_container.adapters.oci.which', side_effect=mock_which):
        result = oci.detect_oci_runtime()
        eq_(result, ("docker", "/usr/bin/docker"))

    def mock_which_singularity(cmd):
        # Only singularity available
        if cmd == "singularity":
            return "/usr/bin/singularity"
        return None

    with patch('datalad_container.adapters.oci.which', side_effect=mock_which_singularity):
        result = oci.detect_oci_runtime()
        eq_(result, ("singularity", "/usr/bin/singularity"))


def test_get_oci_runtime_auto():
    """Test get_oci_runtime with auto mode."""
    def mock_which(cmd):
        if cmd == "docker":
            return "/usr/bin/docker"
        return None

    with patch('datalad_container.adapters.oci.which', side_effect=mock_which):
        result = oci.get_oci_runtime(dataset_path=None)
        eq_(result, ("docker", "/usr/bin/docker"))


def test_get_oci_runtime_no_runtime_available():
    """Test get_oci_runtime raises when no runtime available."""
    with patch('datalad_container.adapters.oci.which', return_value=None):
        with assert_raises(RuntimeError) as cm:
            oci.get_oci_runtime(dataset_path=None)
        assert "No OCI runtime found" in str(cm.value)


def test_get_oci_runtime_specific():
    """Test get_oci_runtime with specific runtime configured."""
    def mock_which(cmd):
        if cmd == "podman":
            return "/usr/bin/podman"
        return None

    env = {"DATALAD_CONTAINERS_RUN_OCI_RUNTIME": "podman"}
    with patch('datalad_container.adapters.oci.which', side_effect=mock_which):
        with patch.dict(os.environ, env):
            result = oci.get_oci_runtime(dataset_path=None)
            eq_(result, ("podman", "/usr/bin/podman"))


def test_get_oci_runtime_not_found():
    """Test get_oci_runtime raises when configured runtime not found."""
    env = {"DATALAD_CONTAINERS_RUN_OCI_RUNTIME": "podman"}
    with patch('datalad_container.adapters.oci.which', return_value=None):
        with patch.dict(os.environ, env):
            with assert_raises(RuntimeError) as cm:
                oci.get_oci_runtime(dataset_path=None)
            assert "not found in PATH" in str(cm.value)


def test_get_oci_runtime_precedence():
    """Test config precedence: env var > dataset > global."""
    def mock_which(cmd):
        # Both podman and docker available
        if cmd in ["podman", "docker"]:
            return f"/usr/bin/{cmd}"
        return None

    # Set environment variable - should take precedence
    env = {"DATALAD_CONTAINERS_RUN_OCI_RUNTIME": "podman"}
    with patch('datalad_container.adapters.oci.which', side_effect=mock_which):
        with patch.dict(os.environ, env):
            # Even with dataset path provided, env var wins
            result = oci.get_oci_runtime(dataset_path="/some/path")
            eq_(result, ("podman", "/usr/bin/podman"))
