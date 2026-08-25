from setuptools import find_packages, setup

package_name = 'mingky_low_obstacle'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'README.md']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Mingky Care Team',
    maintainer_email='mingky-care@example.com',
    description='Ultrasonic and LiDAR fusion for low-profile obstacle avoidance.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'low_obstacle_supervisor = '
            'mingky_low_obstacle.supervisor_node:main',
        ],
    },
)
