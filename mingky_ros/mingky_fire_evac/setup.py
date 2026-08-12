from setuptools import find_packages, setup

package_name = 'mingky_fire_evac'

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
    description='YOLO로 화재를 감지하면 대피 지점으로 이동한다 (실험 단계).',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'fire_evac_node = mingky_fire_evac.fire_evac_node:main',
        ],
    },
)
