from glob import glob

from setuptools import find_packages, setup

package_name = "tangying_ros_gateway"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="SUSTechWLA",
    maintainer_email="opensource@sustechwla.org",
    description="Tangying Robot Gateway ROS 2 bridge",
    license="Apache-2.0",
    entry_points={"console_scripts": ["gateway = tangying_ros_gateway.node:main"]},
)
