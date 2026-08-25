from setuptools import find_packages, setup

package_name = 'mingky_fleet_agent'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Mingky Care Team',
    maintainer_email='mingky-care@example.com',
    description='Fleet coordination link between the robot and the control server.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'fleet_agent = mingky_fleet_agent.fleet_agent:main',
        ],
    },
)
