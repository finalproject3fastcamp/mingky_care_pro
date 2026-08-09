from glob import glob
import os

from setuptools import find_packages, setup


package_name = 'mingky_aruco_detector'


setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml', 'README.md']),
        (f'share/{package_name}/launch', glob(os.path.join('launch', '*.launch.py'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Mingky Care Team',
    maintainer_email='mingky-care@example.com',
    description='ArUco marker detection and calibrated pose estimation.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'aruco_detector = mingky_aruco_detector.aruco_detector_node:main',
        ],
    },
)
