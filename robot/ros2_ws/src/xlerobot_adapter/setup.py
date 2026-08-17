from setuptools import find_packages, setup

package_name = "xlerobot_adapter"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/config", ["config/xlerobot.yaml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="SUSTechWLA",
    maintainer_email="opensource@sustechwla.org",
    description="XLeRobot hardware adapter",
    license="Apache-2.0",
    entry_points={"console_scripts": ["adapter = xlerobot_adapter.node:main"]},
)
