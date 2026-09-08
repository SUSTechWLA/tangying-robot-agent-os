from glob import glob

from setuptools import find_packages, setup

setup(
    name="tangying_navigation",
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/tangying_navigation"]),
        ("share/tangying_navigation", ["package.xml"]),
        ("share/tangying_navigation/launch", glob("launch/*.launch.py")),
        ("share/tangying_navigation/config", glob("config/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="SUSTechWLA",
    maintainer_email="opensource@sustechwla.org",
    license="Apache-2.0",
    description="Tangying RGB-D SLAM and navigation boundary",
    entry_points={
        "console_scripts": [
            "runtime_rgbd_bridge = tangying_navigation.rgbd_bridge:main",
            "navigation_http = tangying_navigation.navigation_node:main",
        ]
    },
)
