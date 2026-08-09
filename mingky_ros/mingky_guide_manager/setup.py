from setuptools import find_packages, setup

package_name = 'mingky_guide_manager'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml']),
        # 저장소 루트의 이벤트 코드 정본을 share 로 함께 설치한다.
        # 설치 후에도 ros2 run 만으로 검증이 동작해야 하기 때문이다.
        (f'share/{package_name}/config', ['../../config/event_codes.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Mingky Care Team',
    maintainer_email='mingky-care@example.com',
    description='Patient guidance manager package for the Mingky Care project.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'guide_manager = mingky_guide_manager.guide_manager_node:main',
        ],
    },
)
