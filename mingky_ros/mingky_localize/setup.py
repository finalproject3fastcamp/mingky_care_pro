from setuptools import find_packages, setup

package_name = 'mingky_localize'

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
    description='AMCL 초기 위치 자동 설정 — RViz 2D Pose Estimate 손조작을 없앤다.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'auto_localize_node = mingky_localize.auto_localize_node:main',
        ],
    },
)
