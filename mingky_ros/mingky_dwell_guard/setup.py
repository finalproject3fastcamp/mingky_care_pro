from setuptools import find_packages, setup

package_name = 'mingky_dwell_guard'

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
    description='환자를 놓친 채 너무 오래 서 있으면 안내를 접고 충전소로 보낸다.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'dwell_guard_node = mingky_dwell_guard.dwell_guard_node:main',
        ],
    },
)
