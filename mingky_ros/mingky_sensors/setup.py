from setuptools import find_packages, setup

package_name = 'mingky_sensors'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='jw',
    maintainer_email='wjddn007658@gmail.com',
    description='I2C ADC 를 단독으로 읽어 배터리·초음파·IR 을 발행한다',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'adc_reader=mingky_sensors.adc_reader:main',
        ],
    },
)
