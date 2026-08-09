import glob
import os

from setuptools import find_packages, setup

package_name = 'mingky_battery_guard'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob.glob(os.path.join('launch', '*launch.*'))),
        # ROS 없이 로봇에서 직접 돌리는 스크립트들
        ('share/' + package_name + '/scripts', glob.glob(os.path.join('scripts', '*.py'))),
        # 로봇 백업 / 바닐라 복원 도구
        ('share/' + package_name + '/robot_tools', glob.glob(os.path.join('robot_tools', '*.sh'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='jw',
    maintainer_email='wjddn007658@gmail.com',
    description='배터리 측정값을 필터링해 저전압 상태 변화를 발행',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'emergency_stop=mingky_battery_guard.emergency_stop:main',
            'battery_guard=mingky_battery_guard.battery_guard:main',
            'fake_battery=mingky_battery_guard.fake_battery:main',
        ],
    },
)
