from importlib.metadata import version

import fabric_pytester


def test_package_version_comes_from_installed_metadata():
    assert fabric_pytester.__version__ == version("fabric-pytester")
