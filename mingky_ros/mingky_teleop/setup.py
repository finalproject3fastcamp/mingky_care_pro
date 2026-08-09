from setuptools import find_packages, setup

package_name = 'mingky_teleop'

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
    description='Speed limiting for human-issued velocity commands.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'teleop_limiter = mingky_teleop.teleop_limiter:main',
            'mode_manager = mingky_teleop.mode_manager:main',
        ],
    },
)
